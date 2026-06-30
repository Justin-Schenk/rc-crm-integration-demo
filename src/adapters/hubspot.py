"""
HubSpot CRM adapter.

Two responsibilities:
1. find_contact(phone_number) -- search HubSpot contacts by phone number
2. log_activity(contact_id, event) -- write a call, SMS, or voicemail note
   to that contact's timeline

Authentication: HubSpot Personal Access Token passed via the
HUBSPOT_ACCESS_TOKEN environment variable.

HubSpot API reference:
- Contacts: https://developers.hubspot.com/docs/api/crm/contacts
- Notes: https://developers.hubspot.com/docs/api/crm/notes
"""

import os
import re
import requests
from datetime import datetime, timezone


HUBSPOT_BASE_URL = "https://api.hubapi.com"


class HubSpotError(Exception):
    pass


def _get_headers():
    token = os.environ.get("HUBSPOT_ACCESS_TOKEN")
    if not token:
        raise HubSpotError("HUBSPOT_ACCESS_TOKEN not set in environment")
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


def _normalize_phone(phone_number):
    """
    Strip all non-numeric characters from a phone number for comparison.
    HubSpot stores numbers in various formats -- (808) 866-6788, 8088666788,
    +18088666788 -- so we strip formatting before comparing.
    """
    return re.sub(r"\D", "", phone_number or "")


def find_contact(phone_number):
    """
    Search HubSpot contacts for a contact with a matching phone number.
    Returns the HubSpot contact ID if found, None if not found.
    """
    normalized = _normalize_phone(phone_number)
    last_ten = normalized[-10:]

    url = f"{HUBSPOT_BASE_URL}/crm/v3/objects/contacts/search"

    payload = {
        "filterGroups": [
            {
                "filters": [
                    {
                        "propertyName": "phone",
                        "operator": "EQ",
                        "value": f"+1{last_ten}",
                    }
                ]
            },
            {
                "filters": [
                    {
                        "propertyName": "phone",
                        "operator": "EQ",
                        "value": f"+1{last_ten}",
                    }
                ]
            },
            {
                "filters": [
                    {
                        "propertyName": "mobilephone",
                        "operator": "EQ",
                        "value": last_ten,
                    }
                ]
            },
            {
                "filters": [
                    {
                        "propertyName": "mobilephone",
                        "operator": "EQ",
                        "value": f"+1{last_ten}",
                    }
                ]
            },
        ],
        "properties": ["firstname", "lastname", "phone", "mobilephone"],
        "limit": 1,
    }

    response = requests.post(url, headers=_get_headers(), json=payload, timeout=15)

    if response.status_code != 200:
        raise HubSpotError(
            f"Contact search failed ({response.status_code}): {response.text}"
        )

    results = response.json().get("results", [])
    if not results:
        return None

    contact = results[0]
    return {
        "id": contact["id"],
        "name": f"{contact['properties'].get('firstname', '')} {contact['properties'].get('lastname', '')}".strip(),
        "phone": contact["properties"].get("phone") or contact["properties"].get("mobilephone"),
    }


def log_activity(contact_id, event):
    """
    Creates a Note on the HubSpot contact timeline recording the
    call, SMS, or voicemail event from RingCentral.
    """
    event_type = event.get("event_type")
    timestamp = event.get("timestamp", datetime.now(timezone.utc).isoformat())
    from_number = event.get("from_number", "Unknown")
    to_number = event.get("to_number", "Unknown")

    if event_type == "call":
        direction = event.get("direction", "Unknown")
        status = event.get("status", "Unknown")
        body = (
            f"RingCentral Call\n"
            f"Direction: {direction}\n"
            f"From: {from_number}\n"
            f"To: {to_number}\n"
            f"Status: {status}\n"
            f"Time: {timestamp}"
        )
    elif event_type == "sms":
        text = event.get("text", "")
        body = (
            f"RingCentral SMS\n"
            f"From: {from_number}\n"
            f"To: {to_number}\n"
            f"Message: {text}\n"
            f"Time: {timestamp}"
        )
    elif event_type == "voicemail":
        note = event.get("crm_note", "Voicemail received")
        body = f"RingCentral Voicemail\n{note}"
    else:
        body = f"RingCentral Event ({event_type})\nTime: {timestamp}"

    note_url = f"{HUBSPOT_BASE_URL}/crm/v3/objects/notes"
    note_payload = {
        "properties": {
            "hs_note_body": body,
            "hs_timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        },
        "associations": [
            {
                "to": {"id": contact_id},
                "types": [
                    {
                        "associationCategory": "HUBSPOT_DEFINED",
                        "associationTypeId": 202,
                    }
                ],
            }
        ],
    }

    response = requests.post(
        note_url, headers=_get_headers(), json=note_payload, timeout=15
    )

    if response.status_code not in (200, 201):
        raise HubSpotError(
            f"Note creation failed ({response.status_code}): {response.text}"
        )

    return response.json()


def create_contact_if_not_found(phone_number, event):
    """
    If no contact is found for an inbound number, create a new one.
    Handles the case where someone not yet in the CRM calls or texts.
    """
    url = f"{HUBSPOT_BASE_URL}/crm/v3/objects/contacts"

    payload = {
        "properties": {
            "phone": phone_number,
            "firstname": "Unknown",
            "lastname": f"Caller {phone_number}",
            "hs_lead_status": "NEW",
        }
    }

    response = requests.post(url, headers=_get_headers(), json=payload, timeout=15)

    if response.status_code not in (200, 201):
        raise HubSpotError(
            f"Contact creation failed ({response.status_code}): {response.text}"
        )

    contact = response.json()
    return {
        "id": contact["id"],
        "name": f"Unknown Caller {phone_number}",
        "phone": phone_number,
    }
