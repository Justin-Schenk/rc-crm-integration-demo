"""
Two-way texting: outbound send. Inbound is handled in webhook_server.py via
the SMS event notification.

Reference: https://developers.ringcentral.com/guide/messaging/sms

Note: the `from_number` used here must belong to a TCR-approved campaign in
production. In sandbox, RingCentral provisions test numbers that bypass TCR
for development purposes -- worth flagging in conversation with Dylan since
it's a real source of "works in dev, fails in prod" bugs on this kind of
integration.
"""

import requests


class SmsSendError(Exception):
    pass


def send_sms(auth, from_number, to_number, text):
    url = f"{auth.server_url}/restapi/v1.0/account/~/extension/~/sms"

    headers = auth.auth_header()
    headers["Content-Type"] = "application/json"

    body = {
        "from": {"phoneNumber": from_number},
        "to": [{"phoneNumber": to_number}],
        "text": text,
    }

    response = requests.post(url, headers=headers, json=body, timeout=15)

    if response.status_code not in (200, 201):
        raise SmsSendError(f"SMS send failed ({response.status_code}): {response.text}")

    return response.json()
