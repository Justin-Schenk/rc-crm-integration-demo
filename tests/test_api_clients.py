"""
Unit tests for the auth, click-to-call, and SMS modules. All HTTP calls are
mocked, so these run without network access and without real RingCentral
credentials -- useful for CI and for proving the logic is correct
independent of any live account.
"""

import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from auth import RingCentralAuth, RingCentralAuthError  # noqa: E402
from click_to_call import RingOutError, initiate_ringout  # noqa: E402
from sms import SmsSendError, send_sms  # noqa: E402


@pytest.fixture
def auth_client():
    return RingCentralAuth(
        server_url="https://platform.devtest.ringcentral.com",
        client_id="test_client_id",
        client_secret="test_client_secret",
        jwt="test.jwt.token",
    )


class TestRingCentralAuth:
    @patch("auth.requests.post")
    def test_get_access_token_success(self, mock_post, auth_client):
        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {"access_token": "fake_token_123", "expires_in": 3600},
        )

        token = auth_client.get_access_token()

        assert token == "fake_token_123"
        mock_post.assert_called_once()
        called_url = mock_post.call_args[0][0]
        assert called_url.endswith("/restapi/oauth/token")

    @patch("auth.requests.post")
    def test_token_is_cached_until_near_expiry(self, mock_post, auth_client):
        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {"access_token": "fake_token_123", "expires_in": 3600},
        )

        first = auth_client.get_access_token()
        second = auth_client.get_access_token()

        assert first == second
        # Only one network call should have happened: the second call hit
        # the cache instead of re-authenticating.
        assert mock_post.call_count == 1

    @patch("auth.requests.post")
    def test_force_refresh_bypasses_cache(self, mock_post, auth_client):
        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {"access_token": "fake_token_123", "expires_in": 3600},
        )

        auth_client.get_access_token()
        auth_client.get_access_token(force_refresh=True)

        assert mock_post.call_count == 2

    @patch("auth.requests.post")
    def test_failed_auth_raises(self, mock_post, auth_client):
        mock_post.return_value = MagicMock(status_code=400, text="invalid_grant")

        with pytest.raises(RingCentralAuthError):
            auth_client.get_access_token()

    @patch("auth.requests.post")
    def test_expired_token_triggers_refresh(self, mock_post, auth_client):
        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {"access_token": "fake_token_123", "expires_in": 3600},
        )
        auth_client.get_access_token()

        # Force the cached token to look expired.
        auth_client._expires_at = time.time() - 10

        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {"access_token": "fake_token_456", "expires_in": 3600},
        )
        token = auth_client.get_access_token()

        assert token == "fake_token_456"
        assert mock_post.call_count == 2


class TestClickToCall:
    @patch("click_to_call.requests.post")
    def test_initiate_ringout_success(self, mock_post, auth_client):
        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {"uri": "https://platform.devtest.ringcentral.com/.../ring-out/abc123",
                          "status": {"callStatus": "InProgress"}},
        )

        with patch.object(auth_client, "auth_header", return_value={"Authorization": "Bearer x"}):
            result = initiate_ringout(auth_client, "+15551234567", "+15559876543")

        assert "uri" in result
        sent_body = mock_post.call_args.kwargs["json"]
        assert sent_body["from"]["phoneNumber"] == "+15551234567"
        assert sent_body["to"]["phoneNumber"] == "+15559876543"

    @patch("click_to_call.requests.post")
    def test_initiate_ringout_failure_raises(self, mock_post, auth_client):
        mock_post.return_value = MagicMock(status_code=403, text="Forbidden")

        with patch.object(auth_client, "auth_header", return_value={"Authorization": "Bearer x"}):
            with pytest.raises(RingOutError):
                initiate_ringout(auth_client, "+15551234567", "+15559876543")

    @patch("click_to_call.requests.post")
    def test_caller_id_included_when_provided(self, mock_post, auth_client):
        mock_post.return_value = MagicMock(status_code=200, json=lambda: {"uri": "x"})

        with patch.object(auth_client, "auth_header", return_value={"Authorization": "Bearer x"}):
            initiate_ringout(auth_client, "+15551234567", "+15559876543", caller_id="+15550001111")

        sent_body = mock_post.call_args.kwargs["json"]
        assert sent_body["callerId"]["phoneNumber"] == "+15550001111"


class TestSms:
    @patch("sms.requests.post")
    def test_send_sms_success(self, mock_post, auth_client):
        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {"id": "msg_123", "messageStatus": "Sent"},
        )

        with patch.object(auth_client, "auth_header", return_value={"Authorization": "Bearer x"}):
            result = send_sms(auth_client, "+15551234567", "+15559876543", "Hello from the demo")

        assert result["messageStatus"] == "Sent"
        sent_body = mock_post.call_args.kwargs["json"]
        assert sent_body["text"] == "Hello from the demo"
        assert sent_body["to"][0]["phoneNumber"] == "+15559876543"

    @patch("sms.requests.post")
    def test_send_sms_failure_raises(self, mock_post, auth_client):
        mock_post.return_value = MagicMock(status_code=422, text="Number not registered for SMS")

        with patch.object(auth_client, "auth_header", return_value={"Authorization": "Bearer x"}):
            with pytest.raises(SmsSendError):
                send_sms(auth_client, "+15551234567", "+15559876543", "test")
