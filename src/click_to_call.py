"""
Click-to-call via the RingOut API.

RingOut is the simplest way to wire a "click to call" button into a CRM:
the API tells RingCentral to call the user's own phone first, then bridge
that call to the destination number. No softphone, no WebRTC, no desk
phone control needed on the agent's side -- just two REST calls plus a
status poll.

Reference: https://developers.ringcentral.com/guide/voice/ringout
"""

import requests


class RingOutError(Exception):
    """Raised when a RingOut request fails outright (not just rings/fails to connect)."""


def initiate_ringout(auth, from_number, to_number, caller_id=None, prompt=False):
    """
    Starts a RingOut call: bridges `from_number` (the agent's own phone,
    e.g. their desk extension or mobile) to `to_number` (the customer,
    pulled from whatever CRM record the click-to-call button lives on).

    Returns the call session resource, which includes a status URL you can
    poll to confirm the call actually connected -- useful for logging the
    outcome back into the CRM.
    """
    url = f"{auth.server_url}/restapi/v1.0/account/~/extension/~/ring-out"

    body = {
        "from": {"phoneNumber": from_number},
        "to": {"phoneNumber": to_number},
        "playPrompt": prompt,
    }
    if caller_id:
        body["callerId"] = {"phoneNumber": caller_id}

    headers = auth.auth_header()
    headers["Content-Type"] = "application/json"

    response = requests.post(url, headers=headers, json=body, timeout=15)

    if response.status_code not in (200, 201):
        raise RingOutError(f"RingOut failed ({response.status_code}): {response.text}")

    return response.json()


def get_ringout_status(auth, ringout_uri):
    """
    Polls the status of an in-progress RingOut call. `ringout_uri` is the
    `uri` field returned by initiate_ringout(). A CRM integration would
    poll this for a few seconds after the click-to-call button fires, then
    write the resulting status (e.g. "Success", "NoAnswer", "Busy") onto
    the activity/timeline record.
    """
    headers = auth.auth_header()
    response = requests.get(ringout_uri, headers=headers, timeout=15)

    if response.status_code != 200:
        raise RingOutError(f"Status check failed ({response.status_code}): {response.text}")

    return response.json()
