"""
Creates and renews the webhook subscription that feeds webhook_server.py.

A single subscription can carry multiple event filters; here we subscribe
to telephony session events (call start/ring/answer/end) and inbound SMS,
since those are the two event types a CRM integration cares about for
"log every call and text against the contact record."

Reference: https://developers.ringcentral.com/guide/notifications/webhooks/creating-webhooks
"""

import requests

# Event filters: telephony sessions cover call state changes, inbound is
# the SMS message-store event. Adjust extensionId/accountId path if
# subscribing on behalf of a specific extension rather than '~' (current).
EVENT_FILTERS = [
    "/restapi/v1.0/account/~/extension/~/telephony/sessions",
    "/restapi/v1.0/account/~/extension/~/message-store/instant?type=SMS",
    "/restapi/v1.0/account/~/extension/~/message-store/instant?type=VoiceMail",
]


class SubscriptionError(Exception):
    pass


def create_subscription(auth, delivery_address, expires_in=604800):
    """
    delivery_address: the publicly reachable HTTPS URL of webhook_server.py
    (e.g. an ngrok URL in dev, or the deployed endpoint in production).

    expires_in: seconds until RingCentral stops sending events and the
    subscription needs renewing. Default here is 7 days; webhook_server.py
    handles the "Renew" reminder event so this can run indefinitely without
    manual intervention.
    """
    url = f"{auth.server_url}/restapi/v1.0/subscription"

    headers = auth.auth_header()
    headers["Content-Type"] = "application/json"

    body = {
        "eventFilters": EVENT_FILTERS,
        "deliveryMode": {
            "transportType": "WebHook",
            "address": delivery_address,
        },
        "expiresIn": expires_in,
    }

    response = requests.post(url, headers=headers, json=body, timeout=15)

    if response.status_code not in (200, 201):
        raise SubscriptionError(f"Subscription create failed ({response.status_code}): {response.text}")

    return response.json()


def renew_subscription(auth, subscription_id):
    url = f"{auth.server_url}/restapi/v1.0/subscription/{subscription_id}/renew"
    response = requests.post(url, headers=auth.auth_header(), timeout=15)

    if response.status_code != 200:
        raise SubscriptionError(f"Renew failed ({response.status_code}): {response.text}")

    return response.json()


if __name__ == "__main__":
    import os
    import sys

    from auth import RingCentralAuth

    if len(sys.argv) != 2:
        print("Usage: python subscribe.py <https-webhook-url>")
        sys.exit(1)

    auth = RingCentralAuth()
    result = create_subscription(auth, sys.argv[1])
    print(f"Subscription created: {result['id']}")
    print(f"Expires: {result['expirationTime']}")
