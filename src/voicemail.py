"""
Voicemail transcription fetcher.

When RingCentral receives a voicemail it automatically transcribes it
(requires RingEX Advanced or Ultra plan, or the Voicemail Transcription
add-on). This module fetches the transcription text from the Message Store
API and formats it as a CRM note ready to be pushed to a contact record.

Reference: https://developers.ringcentral.com/guide/messaging/message-store
"""

import requests


class VoicemailError(Exception):
    pass


def get_voicemail_transcription(auth, message_id, extension_id="~", account_id="~"):
    """
    Fetches a voicemail message record from the Message Store API and
    returns the transcription text if available.

    message_id: the ID of the voicemail message from the webhook event body
    Returns a dict with transcription text and metadata, or None if no
    transcription is available yet (transcription is async and may take
    a few seconds after the voicemail is left).
    """
    url = (
        f"{auth.server_url}/restapi/v1.0/account/{account_id}"
        f"/extension/{extension_id}/message-store/{message_id}"
    )

    response = requests.get(url, headers=auth.auth_header(), timeout=15)

    if response.status_code != 200:
        raise VoicemailError(
            f"Failed to fetch voicemail ({response.status_code}): {response.text}"
        )

    message = response.json()

    # RingCentral only transcribes voicemails, not all message types
    if message.get("type") != "VoiceMail":
        return None

    transcription = message.get("vmTranscriptionStatus")

    # Transcription statuses: NotAvailable, InProgress, TimedOut, Completed
    if transcription != "Completed":
        return {
            "transcription_status": transcription,
            "transcription_text": None,
            "from_number": message.get("from", {}).get("phoneNumber"),
            "to_number": message.get("to", [{}])[0].get("phoneNumber"),
            "duration_seconds": message.get("duration"),
            "timestamp": message.get("creationTime"),
        }

    # Extract transcription text from the message body
    body_text = ""
    for attachment in message.get("attachments", []):
        if attachment.get("type") == "TextTranscription":
            # Fetch the actual transcription text content
            text_url = attachment.get("uri")
            text_response = requests.get(
                text_url, headers=auth.auth_header(), timeout=15
            )
            if text_response.status_code == 200:
                body_text = text_response.text
            break

    return {
        "transcription_status": "Completed",
        "transcription_text": body_text,
        "from_number": message.get("from", {}).get("phoneNumber"),
        "to_number": message.get("to", [{}])[0].get("phoneNumber"),
        "duration_seconds": message.get("duration"),
        "timestamp": message.get("creationTime"),
    }


def format_as_crm_note(transcription_result):
    """
    Formats a voicemail transcription result into a clean CRM note string.
    This is what gets written to the contact record in the CRM.
    """
    if not transcription_result:
        return None

    from_number = transcription_result.get("from_number", "Unknown")
    duration = transcription_result.get("duration_seconds", "Unknown")
    timestamp = transcription_result.get("timestamp", "")
    status = transcription_result.get("transcription_status")
    text = transcription_result.get("transcription_text")

    if status != "Completed" or not text:
        return (
            f"Voicemail received from {from_number} at {timestamp}. "
            f"Duration: {duration}s. "
            f"Transcription status: {status}. "
            f"Transcription not yet available -- check RingCentral for full message."
        )

    return (
        f"Voicemail from {from_number} at {timestamp} ({duration}s):\n\n"
        f"{text.strip()}"
    )
