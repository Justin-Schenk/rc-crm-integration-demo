# RingCentral CRM Integration Demo

A working demonstration of the integration layer that sits on top of a
standard RingCentral deployment: click-to-call, two-way SMS, and a webhook
pipeline that pushes call/text activity into a CRM in real time.

This is deliberately scoped to the piece of a VoIP deployment that a
GUI-driven admin portal setup doesn't cover. Number porting, IVR design,
ring groups, and hardware provisioning are configuration tasks. Wiring a
phone system into a CRM so calls and texts show up against the right
contact automatically is a software integration task, and that's what this
repo demonstrates end to end.

## What it does

```
CRM (or click-to-call button)
        |
        v
click_to_call.py  ----->  RingCentral RingOut API  ----->  bridges agent + customer
sms.py             ----->  RingCentral SMS API       ----->  sends outbound text

RingCentral (call/SMS events)
        |
        v   (webhook push)
webhook_server.py  ----->  normalizes event  ----->  forwards to CRM webhook
        |
        v
crm_mock.py (stand-in for a real CRM's inbound webhook, included so the
             whole pipeline can be demoed without real CRM credentials)
```

## Why JWT auth

This integration runs unattended in the background, not on behalf of a
specific logged-in user clicking through a browser. RingCentral's own
guidance is that JWT (server-to-server) is the right auth flow for that
case, versus the Authorization Code flow which is built for interactive,
per-user login. See `src/auth.py` for the implementation: it exchanges a
JWT credential for an access token and caches that token until shortly
before it expires, rather than re-authenticating on every API call (which
would burn into the Auth API's rate limit).

## Project layout

```
src/
  auth.py            JWT -> access token exchange, with caching
  click_to_call.py   RingOut-based click-to-call
  sms.py              Outbound SMS send
  subscribe.py        Creates the webhook subscription (run once per env)
  webhook_server.py   Receives RC events, normalizes, forwards to CRM
  crm_mock.py          Stand-in CRM endpoint, for local demo only
tests/
  test_api_clients.py     Auth, RingOut, SMS -- all HTTP mocked
  test_webhook_server.py  Validation handshake, dedup, event normalization
```

## Setup

1. In the RingCentral Developer Console, create a REST API App with Auth
   Type set to "JWT auth flow," then create a personal JWT credential
   under your profile menu (Credentials > Create JWT). Use a sandbox app
   while developing; sandbox and production JWTs are not interchangeable.
2. `cp .env.example .env` and fill in `RC_SERVER_URL`, `RC_CLIENT_ID`,
   `RC_CLIENT_SECRET`, `RC_JWT`.
3. `pip install -r requirements.txt`
4. Run the test suite (no real credentials needed for this step, since all
   HTTP calls are mocked): `pytest tests/ -v`

## Running the full pipeline locally

1. Start the mock CRM: `python src/crm_mock.py` (port 5001)
2. Start the webhook receiver: `python src/webhook_server.py` (port 5000)
3. Expose port 5000 publicly so RingCentral can reach it, e.g.
   `ngrok http 5000`, since RingCentral's webhook delivery requires a
   public HTTPS endpoint and won't reach `localhost` directly.
4. Create the subscription against your ngrok URL:
   `python src/subscribe.py https://<your-ngrok-id>.ngrok.io/webhook`
5. Place a real call or send a real SMS to your sandbox number. The event
   should flow: RingCentral -> webhook_server.py -> crm_mock.py. Check
   `crm_mock.py`'s console output, or `GET http://localhost:5001/crm-events`
   to see everything logged so far.
6. To test click-to-call independent of the webhook flow:
   `python -c "from src.auth import RingCentralAuth; from src.click_to_call import initiate_ringout; a = RingCentralAuth(); print(initiate_ringout(a, '+1XXXXXXXXXX', '+1YYYYYYYYYY'))"`

## What's intentionally left out

This is a scoped demo, not a production integration. Things a real
deployment would add on top of this:

- Persistent storage for the dedup cache and subscription state (currently
  in-memory, so it resets on restart and won't work correctly with
  multiple server instances behind a load balancer).
- A reconciliation job against the Call Log / Message Store APIs, since
  RingCentral does not guarantee webhook delivery and a CRM activity log
  that silently drops events is worse than one that's a few minutes
  delayed.
- Retry queue for failed CRM forwarding instead of logging and dropping.
- Per-client CRM adapters, since `crm_mock.py` stands in for whatever the
  client's real CRM webhook contract actually looks like (HubSpot,
  Salesforce, ServiceTitan, etc. all expect different payload shapes).
