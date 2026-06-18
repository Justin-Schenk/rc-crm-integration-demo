"""
Stand-in for a real CRM's incoming-webhook endpoint, so the whole pipeline
(RingCentral -> webhook_server.py -> "CRM") can be demoed end-to-end on a
laptop without needing actual HubSpot/Salesforce/ServiceTitan credentials.

In a real deployment this file doesn't exist; CRM_WEBHOOK_URL in
webhook_server.py would point at the client's actual CRM webhook
(HubSpot workflow webhook trigger, Salesforce inbound API, etc).
"""

from flask import Flask, jsonify, request

app = Flask(__name__)

# In-memory activity log, just for the demo. A real CRM would create an
# actual timeline/activity record against the matching contact.
received_events = []


@app.route("/crm-events", methods=["POST"])
def crm_events():
    event = request.get_json(silent=True) or {}
    received_events.append(event)
    print(f"[CRM] Logged activity: {event}")
    return jsonify({"status": "received"}), 200


@app.route("/crm-events", methods=["GET"])
def list_events():
    return jsonify(received_events), 200


if __name__ == "__main__":
    app.run(port=5001, debug=True)
