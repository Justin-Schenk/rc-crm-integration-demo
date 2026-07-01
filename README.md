# RingCentral CRM Integration Demo

A working demo of the middleware integration layer which connects RingCentral's REST API to a CRM. Built to show how integrating the APIs unlocks the full value of a client's RingCentral Advanced or Ultra subscription by connecting their phone system directly to their CRM.

This demo uses HubSpot as the target CRM. The adapter pattern means any CRM with a REST API can be added as a new adapter file without touching the core pipeline. I will be adding additional CRM integration capabilities in future updates.

## What it does

When a call, SMS, or voicemail event comes in on a RingCentral account:

1. RingCentral pushes the event to the webhook server via a subscription
2. The webhook server normalizes the event
3. The integration looks up the caller's phone number in the CRM
4. If a matching contact is found, a note is logged automatically
5. If no contact is found, a new contact is created

There is no manual data entry. Every interaction is logged automatically to the right contact record, or a new contact is created.

## Who this is for

Small businesses on RingCentral Advanced or Ultra who need their phone system connected to their CRM. RingCentral does not provide this configuration service for small business clients. This fills that gap.

## RingCentral tier requirements

| Feature | Core | Advanced | Ultra |
|---|---|---|---|
| Basic phone system | Yes | Yes | Yes |
| CRM integration | No | Yes | Yes |
| Automatic call recording | No | Yes | Yes |
| Voicemail transcription | No | No | Yes |
| AI call summaries | No | No | Yes |

This integration requires **Advanced or Ultra**. Core clients need to upgrade before integration.

## Architecture

```
RingCentral (call/SMS/voicemail event)
        |
        v  (webhook push)
webhook_server.py
        |
        v
normalize event (call / SMS / voicemail)
        |
        v
adapters/hubspot.py
        |
        ├── find_contact(phone_number) --> Hubspot Contacts API
        |
        └── log_activity(contact_id, event) --> Hubspot Notes API
```

## Adding a new CRM

Each CRM is a single adapter file in `src/adapters/`. Every adapter implements two functions:

```python
def find_contact(phone_number):
    # search this CRM for a contact with that phone number
    # return contact dict with id, name, phone -- or None

def log_activity(contact_id, event):
    # write the event as an activity/note to that contact record
```

To switch a client from HubSpot to Salesforce for example, change one line in `webhook_server.py`:

```python
from adapters import hubspot as crm   # current
from adapters import salesforce as crm  # switch to Salesforce (work in progress)
```

## Project layout

```
src/
  auth.py              JWT auth against RingCentral API
  webhook_server.py    Receives RC events, normalizes, routes to CRM adapter
  subscribe.py         Creates the webhook subscription (run once per client)
  click_to_call.py     RingOut click-to-call implementation
  sms.py               Outbound SMS send
  voicemail.py         Fetches transcribed voicemail from RC Message Store API
  crm_mock.py          Stand-in CRM endpoint for testing without a real CRM
  adapters/
    hubspot.py         HubSpot CRM adapter
tests/
  test_api_clients.py      Auth, RingOut, SMS unit tests
  test_webhook_server.py   Webhook validation, dedup, normalization tests
```

## Setup

### Prerequisites

- RingCentral Advanced or Ultra account
- RingCentral Developer Console app with JWT auth and these scopes:
  - Read Accounts, Ring Out, SMS, Webhook Subscriptions, Read Messages
- HubSpot account with a Service Key configured with these scopes:
  - crm.objects.contacts.read/write
  - crm.objects.custom.read/write
  - crm.extensions_calling_transcripts.read/write

### Environment variables

```bash
cp .env.example .env
```

Fill in `.env`:

```
RC_SERVER_URL=https://platform.ringcentral.com
RC_CLIENT_ID=your_rc_client_id
RC_CLIENT_SECRET=your_rc_client_secret
RC_JWT=your_rc_jwt
HUBSPOT_ACCESS_TOKEN=your_hubspot_service_key
CRM_WEBHOOK_URL=http://localhost:5001/crm-events
```

### Install dependencies

```bash
pip install -r requirements.txt
```

### Run the tests

```bash
pytest tests/ -v
```

## Running the demo

Open four terminals:

**Terminal 1 -- Mock CRM (optional, for testing without HubSpot):**
```bash
python3 src/crm_mock.py
```

**Terminal 2 -- Webhook server:**
```bash
python3 src/webhook_server.py
```

**Terminal 3 -- ngrok (exposes webhook server publicly):**
```bash
ngrok http 5000
```

**Terminal 4 -- Create webhook subscription:**
```bash
python3 -c "
from dotenv import load_dotenv
load_dotenv()
from src.auth import RingCentralAuth
from src.subscribe import create_subscription
auth = RingCentralAuth()
result = create_subscription(auth, 'https://YOUR_NGROK_URL/webhook')
print('Subscription created:', result['id'])
"
```

### Test the pipeline

**Simulate an inbound call:** # change Event-Id, uuid, sessionId, and timestamp as needed. Update the from and to phone numbers before running
```bash
curl -X POST http://localhost:5000/webhook \
  -H "Content-Type: application/json" \
  -H "Event-Id: test-call-XXX" \
  -d '{
    "uuid": "test-call-XXX",
    "event": "/restapi/v1.0/account/~/extension/~/telephony/sessions",
    "timestamp": "2026-06-30T16:00:00.000Z",
    "body": {
      "sessionId": "sess-XXX",
      "parties": [{
        "direction": "Inbound",
        "from": {"phoneNumber": "+1XXXXXXXXXX"},
        "to": {"phoneNumber": "+1YYYYYYYYYY"},
        "status": {"code": "Answered"}
      }]
    }
  }'
```

**Simulate an inbound SMS:** # change Event-Id, uuid, sessionId, and timestamp as needed. Update the from and to phone numbers before running 
```bash
curl -X POST http://localhost:5000/webhook \
  -H "Content-Type: application/json" \
  -H "Event-Id: test-sms-XXX" \
  -d '{
    "uuid": "test-sms-XXX",
    "event": "/restapi/v1.0/account/~/extension/~/message-store",
    "timestamp": "2026-06-30T16:05:00.000Z",
    "body": {
      "id": "msg-XXX",
      "type": "SMS",
      "from": {"phoneNumber": "+1XXXXXXXXXX"},
      "to": [{"phoneNumber": "+1YYYYYYYYYY"}],
      "subject": "Your message text here"
    }
  }'
```

Replace `+1XXXXXXXXXX` with a phone number that exists as a contact in your HubSpot account.

## Key design decisions

- **JWT auth with token caching**: RingCentral's auth endpoint is rate limited. Tokens are cached and reused until near expiration rather than re-authenticating on every call.
- **Deduplication**: RingCentral may deliver the same event more than once. Events are deduplicated by UUID with a bounded in-memory cache.
- **Unknown caller handling**: If no contact is found for an inbound number, a new contact is created automatically and tagged as a new lead.
- **Adapter pattern**: The core pipeline never changes between clients. Only the adapter file changes based on which CRM the client uses.
- **Phone number normalization**: Strips all formatting before comparison so numbers stored as `(555)111-2222`, `5551112222`, or `+15551112222` all match correctly.
