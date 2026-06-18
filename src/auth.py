"""
RingCentral JWT auth client.

Server-to-server authentication pattern: exchanges a JWT credential (created
in the RingCentral Developer Console, bound to a service-user extension) for
a short-lived OAuth access token. This is the auth flow recommended for
back-end integrations with no interactive user login -- exactly what a
CRM/messaging integration needs, since it runs unattended.

Reference: https://developers.ringcentral.com/guide/authentication/jwt-flow
"""

import base64
import os
import time

import requests


class RingCentralAuthError(Exception):
    """Raised when token acquisition fails."""


class RingCentralAuth:
    def __init__(self, server_url=None, client_id=None, client_secret=None, jwt=None):
        # Pull from environment if not passed explicitly so credentials never
        # end up hard-coded in source.
        self.server_url = server_url or os.environ["RC_SERVER_URL"]
        self.client_id = client_id or os.environ["RC_CLIENT_ID"]
        self.client_secret = client_secret or os.environ["RC_CLIENT_SECRET"]
        self.jwt = jwt or os.environ["RC_JWT"]

        self._access_token = None
        self._expires_at = 0  # epoch seconds

    def _basic_auth_header(self):
        raw = f"{self.client_id}:{self.client_secret}".encode("utf-8")
        return base64.b64encode(raw).decode("utf-8")

    def get_access_token(self, force_refresh=False):
        """
        Returns a valid access token, reusing the cached one until it's
        within 60 seconds of expiring. RingCentral's Auth API is rate
        limited, so re-using tokens (rather than re-authenticating on every
        call) matters in production, not just for performance.
        """
        if not force_refresh and self._access_token and time.time() < self._expires_at - 60:
            return self._access_token

        url = f"{self.server_url}/restapi/oauth/token"
        headers = {
            "Authorization": f"Basic {self._basic_auth_header()}",
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        }
        data = {
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
            "assertion": self.jwt,
        }

        response = requests.post(url, headers=headers, data=data, timeout=15)

        if response.status_code != 200:
            raise RingCentralAuthError(
                f"Token request failed ({response.status_code}): {response.text}"
            )

        payload = response.json()
        self._access_token = payload["access_token"]
        self._expires_at = time.time() + payload.get("expires_in", 3600)
        return self._access_token

    def auth_header(self):
        return {"Authorization": f"Bearer {self.get_access_token()}"}
