"""
Webhook receiver: receives RingCentral telephony, SMS, and voicemail events,
normalizes them, looks up the contact in HubSpot by phone number, and logs
the activity to their contact record automatically.

Three things handled that are easy to get wrong:
1. Validation handshake: echo Validation-Token header or get SUB-521.
2. Deduplication: events may arrive more than once; dedupe on UUID.
3. No guaranteed delivery: reconcile against Call Log / Message Store APIs
   in production rather than trusting webhooks as sole source of truth.
"""

import collections
import os
import sys

import requests
from dotenv import load_dotenv
from flask import Flask, jsonify, request

load_dotenv()

sys.path.insert(0, os.path.dirname(__file__))

from auth import RingCentralAuth
from voicemail import format_as_crm_note, get_voicemail_transcription
from adapters import hubspot as crm

app = Flask(__name__)

_seen_event_ids = collections.deque(maxlen=2000)
_seen_event_id_set = set()

_auth = None


def get_auth():
    global _auth
    if _auth is None:
        _auth = RingCentralAuth()
    return _auth


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
    message = body.get("body", {})
    return {
        "event_type": "sms",
        "message_id": message.get("id"),
        "from_number": (message.get("from") or {}).get("phoneNumber"),
        "to_number": [t.get("phoneNumber") for t in message.get("to", [])],
        "text": message.get("subject"),
        "timestamp": body.get("timestamp"),
    }


def _normalize_voicemail_event(body):
    message = body.get("body", {})
    message_id = message.get("id")

    try:
        transcription = get_voicemail_transcription(get_auth(), message_id)
        note = format_as_crm_note(transcription)
    except Exception as exc:
        app.logger.error("Failed to fetch voicemail transcription: %s", exc)
        note = f"Voicemail received (message ID: {message_id}). Check RingCentral for full message."

    return {
        "event_type": "voicemail",
        "message_id": message_id,
        "from_number": (message.get("from") or {}).get("phoneNumber"),
        "to_number": [t.get("phoneNumber") for t in message.get("to", [])],
        "crm_note": note,
        "timestamp": body.get("timestamp"),
    }


def _process_event(event):
    from_number = event.get("from_number")

    if not from_number:
        app.logger.warning("Event has no from_number, skipping CRM lookup")
        return

    try:
        contact = crm.find_contact(from_number)

        if not contact:
            app.logger.info(f"No contact found for {from_number}, creating new contact")
            contact = crm.create_contact_if_not_found(from_number, event)

        crm.log_activity(contact["id"], event)
        app.logger.info(f"Logged {event['event_type']} to HubSpot contact {contact['name']} ({contact['id']})")

    except Exception as exc:
        app.logger.error(f"Failed to log event to HubSpot: %s", exc)


@app.route("/webhook", methods=["POST"])
def webhook():
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
    inner = body.get("body", {})

    if "telephony/sessions" in event_path:
        normalized = _normalize_telephony_event(body)
    elif "message-store" in event_path:
        msg_type = inner.get("type", "")
        if msg_type == "VoiceMail":
            normalized = _normalize_voicemail_event(body)
        else:
            normalized = _normalize_sms_event(body)
    else:
        return jsonify({"status": "unrecognized event type, ignored"}), 200

    _process_event(normalized)
    return jsonify({"status": "logged to HubSpot"}), 200


if __name__ == "__main__":
    app.run(port=5000, debug=True)
