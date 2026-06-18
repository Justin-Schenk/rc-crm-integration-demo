"""
Webhook receiver: this is the piece that actually makes the CRM integration
work. RingCentral pushes raw telephony/SMS events here; this normalizes
them into a simple shape and forwards them to whatever CRM webhook endpoint
is configured, so the CRM gets "call logged" / "text received" activity
without anyone touching the RingCentral admin portal.

Three things this handles that are easy to get wrong on a first pass:

1. Validation handshake: on subscription creation, RingCentral sends one
   request carrying a `Validation-Token` header with no body. You must
   echo that header back, or the subscription creation fails with
   SUB-521 ("WebHook is not reachable").
2. Deduplication: RingCentral states events may be delivered more than
   once and may arrive out of order. We dedupe on event UUID with a
   bounded in-memory set.
3. No guaranteed delivery: RingCentral retries failed deliveries for 24
   hours, but never guarantees every event arrives. A production version
   of this should reconcile against the Call Log / Message Store APIs
   periodically rather than trusting webhooks as the sole source of truth.

Reference: https://developers.ringcentral.com/guide/notifications/webhooks/creating-webhooks
"""

import collections
import os

import requests
from flask import Flask, jsonify, request

app = Flask(__name__)

CRM_WEBHOOK_URL = os.environ.get("CRM_WEBHOOK_URL", "http://localhost:5001/crm-events")

# Bounded dedup cache: holds the last N event UUIDs seen. A real deployment
# would back this with Redis or a DB row with a unique constraint instead
# of an in-memory deque, since this resets on every restart and doesn't
# survive multiple server instances.
_seen_event_ids = collections.deque(maxlen=2000)
_seen_event_id_set = set()


def _already_seen(event_id):
    if event_id in _seen_event_id_set:
        return True
    if len(_seen_event_ids) == _seen_event_ids.maxlen:
        evicted = _seen_event_ids[0]
        _seen_event_id_set.discard(evicted)
    _seen_event_ids.append(event_id)
    _seen_event_id_set.add(event_id)
    return False


def _normalize_telephony_event(body):
    """Pulls the fields a CRM activity log actually needs out of a
    telephony session event payload."""
    session = body.get("body", {})
    parties = session.get("parties", [])
    party = parties[0] if parties else {}

    return {
        "event_type": "call",
        "session_id": session.get("sessionId"),
        "direction": party.get("direction"),
        "from_number": party.get("from", {}).get("phoneNumber"),
        "to_number": party.get("to", {}).get("phoneNumber"),
        "status": party.get("status", {}).get("code"),
        "timestamp": body.get("timestamp"),
    }


def _normalize_sms_event(body):
    """Pulls the fields needed to log an inbound text as a CRM activity."""
    message = body.get("body", {})

    return {
        "event_type": "sms",
        "message_id": message.get("id"),
        "from_number": (message.get("from") or {}).get("phoneNumber"),
        "to_number": [t.get("phoneNumber") for t in message.get("to", [])],
        "text": message.get("subject"),
        "timestamp": body.get("timestamp"),
    }


def _forward_to_crm(payload):
    try:
        requests.post(CRM_WEBHOOK_URL, json=payload, timeout=5)
    except requests.RequestException as exc:
        # In production: push to a retry queue instead of dropping it.
        app.logger.error("Failed to forward event to CRM: %s", exc)


@app.route("/webhook", methods=["POST"])
def webhook():
    # Step 1: validation handshake. RingCentral sends this once when the
    # subscription is first created, with no JSON body.
    validation_token = request.headers.get("Validation-Token")
    if validation_token:
        response = jsonify({})
        response.headers["Validation-Token"] = validation_token
        return response, 200

    body = request.get_json(silent=True) or {}
    event_id = request.headers.get("Event-Id") or body.get("uuid")

    if event_id and _already_seen(event_id):
        return jsonify({"status": "duplicate, ignored"}), 200

    event_path = body.get("event", "")

    if "telephony/sessions" in event_path:
        normalized = _normalize_telephony_event(body)
    elif "message-store" in event_path:
        normalized = _normalize_sms_event(body)
    else:
        return jsonify({"status": "unrecognized event type, ignored"}), 200

    _forward_to_crm(normalized)
    return jsonify({"status": "forwarded"}), 200


if __name__ == "__main__":
    app.run(port=5000, debug=True)
