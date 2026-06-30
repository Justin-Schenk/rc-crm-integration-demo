"""
Tests for webhook_server.py. These exercise the three behaviors that are
easy to get wrong on a first implementation: the validation handshake,
duplicate-event suppression, and normalizing the two event shapes RC sends
(telephony session vs SMS) into one consistent payload before forwarding.
"""

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import webhook_server  # noqa: E402


@pytest.fixture
def client():
    webhook_server.app.config["TESTING"] = True
    # Reset dedup state between tests so they don't bleed into each other.
    webhook_server._seen_event_ids.clear()
    webhook_server._seen_event_id_set.clear()
    return webhook_server.app.test_client()


class TestValidationHandshake:
    def test_validation_token_is_echoed_back(self, client):
        response = client.post(
            "/webhook",
            headers={"Validation-Token": "some-random-token-abc"},
        )

        assert response.status_code == 200
        assert response.headers["Validation-Token"] == "some-random-token-abc"


class TestDeduplication:
    def test_duplicate_event_id_is_ignored(self, client):
        payload = {
            "uuid": "evt-001",
            "event": "/restapi/v1.0/account/~/extension/~/telephony/sessions",
            "timestamp": "2026-06-17T12:00:00.000Z",
            "body": {"sessionId": "abc", "parties": []},
        }

        with patch.object(webhook_server, "_forward_to_crm") as mock_forward:
            first = client.post("/webhook", json=payload, headers={"Event-Id": "evt-001"})
            second = client.post("/webhook", json=payload, headers={"Event-Id": "evt-001"})

        assert first.status_code == 200
        assert second.get_json()["status"] == "duplicate, ignored"
        # The CRM should only get the event once, not twice.
        assert mock_forward.call_count == 1


class TestEventNormalization:
    def test_telephony_event_is_forwarded_with_correct_shape(self, client):
        payload = {
            "uuid": "evt-002",
            "event": "/restapi/v1.0/account/~/extension/~/telephony/sessions",
            "timestamp": "2026-06-17T12:00:00.000Z",
            "body": {
                "sessionId": "sess-1",
                "parties": [
                    {
                        "direction": "Inbound",
                        "from": {"phoneNumber": "+15551112222"},
                        "to": {"phoneNumber": "+15553334444"},
                        "status": {"code": "Answered"},
                    }
                ],
            },
        }

        with patch.object(webhook_server, "_forward_to_crm") as mock_forward:
            response = client.post("/webhook", json=payload, headers={"Event-Id": "evt-002"})

        assert response.status_code == 200
        forwarded = mock_forward.call_args[0][0]
        assert forwarded["event_type"] == "call"
        assert forwarded["direction"] == "Inbound"
        assert forwarded["from_number"] == "+15551112222"
        assert forwarded["status"] == "Answered"

    def test_sms_event_is_forwarded_with_correct_shape(self, client):
        payload = {
            "uuid": "evt-003",
            "event": "/restapi/v1.0/account/~/extension/~/message-store/instant",
            "timestamp": "2026-06-17T12:05:00.000Z",
            "body": {
                "id": "msg-1",
                "from": {"phoneNumber": "+15551112222"},
                "to": [{"phoneNumber": "+15553334444"}],
                "subject": "Hey, running 10 minutes late",
            },
        }

        with patch.object(webhook_server, "_forward_to_crm") as mock_forward:
            response = client.post("/webhook", json=payload, headers={"Event-Id": "evt-003"})

        assert response.status_code == 200
        forwarded = mock_forward.call_args[0][0]
        assert forwarded["event_type"] == "sms"
        assert forwarded["from_number"] == "+15551112222"
        assert forwarded["text"] == "Hey, running 10 minutes late"

    def test_unrecognized_event_type_is_ignored_not_errored(self, client):
        payload = {
            "uuid": "evt-004",
            "event": "/restapi/v1.0/account/~/extension/~/some-other-event",
            "body": {},
        }

        with patch.object(webhook_server, "_forward_to_crm") as mock_forward:
            response = client.post("/webhook", json=payload, headers={"Event-Id": "evt-004"})

        assert response.status_code == 200
        mock_forward.assert_not_called()


class TestVoicemailEvent:
    def test_voicemail_event_is_forwarded_with_transcription(self, client):
        payload = {
            "uuid": "evt-005",
            "event": "/restapi/v1.0/account/~/extension/~/message-store/instant",
            "timestamp": "2026-06-30T10:00:00.000Z",
            "body": {
                "id": "vm-msg-001",
                "type": "VoiceMail",
                "from": {"phoneNumber": "+15551112222"},
                "to": [{"phoneNumber": "+15553334444"}],
            },
        }

        mock_transcription = {
            "transcription_status": "Completed",
            "transcription_text": "Hi, this is John. Just calling to confirm my appointment on Friday.",
            "from_number": "+15551112222",
            "to_number": "+15553334444",
            "duration_seconds": 12,
            "timestamp": "2026-06-30T10:00:00.000Z",
        }

        with patch("webhook_server.get_auth", return_value=None), \
             patch("webhook_server.get_voicemail_transcription", return_value=mock_transcription), \
             patch.object(webhook_server, "_forward_to_crm") as mock_forward:
            response = client.post("/webhook", json=payload, headers={"Event-Id": "evt-005"})

        assert response.status_code == 200
        forwarded = mock_forward.call_args[0][0]
        assert forwarded["event_type"] == "voicemail"
        assert forwarded["message_id"] == "vm-msg-001"
        assert "John" in forwarded["crm_note"]
        assert "appointment" in forwarded["crm_note"]

    def test_voicemail_transcription_in_progress(self, client):
        payload = {
            "uuid": "evt-006",
            "event": "/restapi/v1.0/account/~/extension/~/message-store/instant",
            "timestamp": "2026-06-30T10:01:00.000Z",
            "body": {
                "id": "vm-msg-002",
                "type": "VoiceMail",
                "from": {"phoneNumber": "+15551112222"},
                "to": [{"phoneNumber": "+15553334444"}],
            },
        }

        mock_transcription = {
            "transcription_status": "InProgress",
            "transcription_text": None,
            "from_number": "+15551112222",
            "to_number": "+15553334444",
            "duration_seconds": 8,
            "timestamp": "2026-06-30T10:01:00.000Z",
        }

        with patch("webhook_server.get_auth", return_value=None), \
             patch("webhook_server.get_voicemail_transcription", return_value=mock_transcription), \
             patch.object(webhook_server, "_forward_to_crm") as mock_forward:
            response = client.post("/webhook", json=payload, headers={"Event-Id": "evt-006"})

        assert response.status_code == 200
        forwarded = mock_forward.call_args[0][0]
        assert forwarded["event_type"] == "voicemail"
        assert "InProgress" in forwarded["crm_note"]
