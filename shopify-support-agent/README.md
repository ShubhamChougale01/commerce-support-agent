# Commerce Support AI Agent

A production-ready AI customer-support agent that automatically resolves
**Zoho Desk** tickets for a **Medusa.js** store using a **two-pass Claude
architecture**: one pass classifies the ticket, a second pass writes the
customer reply. Every auto-reply runs through a quality gate, and anything
risky (fraud, legal threats, large refunds, low confidence, PII leaks) is
routed to a human with a full handoff brief.

> Historical note: the project folder is named `shopify-support-agent` because
> v1 targeted Shopify + Freshdesk. The clients were swapped for Medusa.js and
> Zoho Desk behind the same internal interfaces (see `plan/MIGRATION_PLAN.md`).

**Status (2026-06-14):** code complete; CI green (9/9); all 5 flows verified
live end-to-end (local Medusa + live Zoho Desk + Supabase). Remaining:
`SLACK_WEBHOOK_URL` + deploy. Live checklist: **BLOCKERS.md**.

---

## Architecture

```
Zoho Desk workflow webhook
       │
       ▼
POST /webhook/desk ──► X-Webhook-Secret verify ──► idempotency check (Supabase)
       │                                                  │
       │                                           (already seen → 200)
       ▼
BackgroundTask: run_agent(ticket_id)
       │
  1. get_ticket()           Zoho Desk  ── contact, body, subject
  2. enrich order           Medusa     ── by #display_id, else by email
  3. get_ticket_thread()    Zoho Desk  ── multi-turn context
  4. classify_ticket()      Claude Sonnet  (PASS 1 → JSON)
  5. escalate? ─── yes ──►  escalate_to_human()  ──► STOP
  6. commerce action        refund ≤ limit → issue_refund (2-step in Medusa)
                            refund > limit / cancel → escalate ──► STOP
  7. generate_reply()       Claude Sonnet  (PASS 2 → email body)
  8. quality_gate           PII / length / policy
                            fail → escalate (with draft) ──► STOP
  9. post_reply() + log     Zoho Desk + Supabase

Background: APScheduler runs sla_watchdog() every 15 min → Slack warning at 75% of SLA.
```

**Escalation always wins.** Any unhandled exception in `run_agent` is caught and
turned into a `system_error` handoff, so a ticket is never silently dropped.

---

## Project layout

```
app/
  main.py          FastAPI app, webhook, health, lifespan
  agent.py         run_agent() — the 9-step orchestrator
  medusa.py        Medusa v2 Admin API client (httpx + tenacity)
  zohodesk.py      Zoho Desk client (OAuth token manager + httpx + tenacity)
  escalation.py    handoff brief, customer ack, Slack, Supabase log, routing
  quality_gate.py  PII / length / policy checks
  brand_config.py  per-client voice + policy
  watchdog.py      SLA watchdog (APScheduler)
  config.py        env loading, ROUTING (team IDs), PRIORITY_MAP, constants
prompts/
  classifier.py        PASS 1 system prompt + classify_ticket()
  reply_generator.py   PASS 2 prompt builder + generate_reply()
scripts/
  seed_supabase.sql    table DDL
tests/
  test_classifier.py   8 fixtures (integration — live Claude)
  test_clients.py      Medusa/Zoho smoke (integration)
  test_e2e.py          mocked pipeline (CI-runnable)
docker-compose.medusa.yml   local Postgres+Redis for a Medusa dev instance
```

---

## Setup

Requires Python 3.11+. Full account walkthrough: **SETUP_GUIDE.md**.

```bash
pip install -r requirements.txt
cp .env.example .env   # then fill in the values
```

### Environment variables

| Variable | Purpose |
|---|---|
| `ANTHROPIC_API_KEY` | Claude API key. **Optional** — if blank, the app falls back to the Claude Code subscription via `claude-agent-sdk` (see *Claude authentication* below). Set a real key for production. |
| `ZOHO_CLIENT_ID` / `ZOHO_CLIENT_SECRET` / `ZOHO_REFRESH_TOKEN` | OAuth Self Client credentials (api-console.zoho.com) |
| `ZOHO_ORG_ID` | Zoho Desk organisation ID (Setup → Developer Space → API) |
| `ZOHO_DC` | data centre: `com`, `in`, `eu`, `com.au` |
| `ZOHO_FROM_EMAIL` | support mailbox replies are sent from |
| `ZOHO_TEAM_FRAUD/LEGAL/REFUNDS/GENERAL` | team IDs for escalation routing |
| `MEDUSA_URL` / `MEDUSA_API_KEY` | Medusa backend + secret API key |
| `SUPABASE_URL` / `SUPABASE_KEY` | Supabase project + service_role key |
| `SLACK_WEBHOOK_URL` | Incoming webhook for P1 / SLA alerts |
| `DESK_WEBHOOK_SECRET` | shared secret for the Zoho workflow webhook |

### Claude authentication

The app needs to call Claude. `prompts/_llm_client.py::get_client()` picks the
backend automatically:

- **`ANTHROPIC_API_KEY` set** → real `anthropic.AsyncAnthropic` (production path).
- **`ANTHROPIC_API_KEY` blank** → `AgentSDKAnthropic`, a shim over
  `claude-agent-sdk` that uses the logged-in `claude` CLI subscription — no API
  key required. Run `pip install claude-agent-sdk` and `claude login` first.

The subscription path is for local/dev. Anthropic does not approve subscription
auth for automated backend services, so production should set a real API key.

### Database

Run `scripts/seed_supabase.sql` in the Supabase SQL editor (or via the
Supabase MCP) to create `escalations`, `processed_tickets`, and
`tickets_processed`.

### Local Medusa

```bash
docker compose -f docker-compose.medusa.yml up -d
npx create-medusa-app@latest medusa-store \
  --db-url postgres://medusa:medusa@localhost:5433/medusa
cd medusa-store && npm run dev    # admin at http://localhost:9000/app
```
Then in the admin UI create a **secret API key** (Settings → API Key
Management) and put it in `.env`.

---

## Run locally

```bash
uvicorn app.main:app --reload --port 8000
curl localhost:8000/health        # {"status":"ok"}
```

## Run with Docker

```bash
docker build -t support-agent .
docker run --rm -p 8000:8000 --env-file .env support-agent
```

The container honours `$PORT` (defaults to 8000), so it deploys to Railway or
Render with no changes.

---

## Tests

```bash
# Fast, no external calls — safe for CI:
pytest -m "not integration"

# Live API tests (need real credentials in .env):
export TEST_EMAIL=known-customer@example.com
export TEST_TICKET_ID=123456789
pytest -m integration
```

---

## Zoho Desk webhook setup

1. **Setup → Automation → Workflows → Create Rule** on module *Tickets*,
   trigger *Create*.
2. Action: **Webhook** → `POST https://<your-host>/webhook/desk`
3. Custom header: `X-Webhook-Secret: <DESK_WEBHOOK_SECRET from .env>`
4. Body (JSON): `{"ticket_id": "${ticketId}"}`

The endpoint verifies the secret (constant-time), checks idempotency in
Supabase, and processes the ticket in a background task.

---

## How escalation routing works

`app/config.py` maps each escalation reason to a Zoho Desk team, priority,
SLA, and Slack channel:

| Reason | Zoho priority | SLA | Channel |
|---|---|---|---|
| `fraud_flag` | High (P1) | 1 hr | `#fraud-alerts` |
| `legal_threat` | High (P1) | 2 hr | `#legal-escalations` |
| `refund_above_threshold` | High (P2) | 4 hr | `#refund-review` |
| `abusive_language`, `quality_gate_fail`, `low_confidence`, `human_requested` | Medium (P3) | 8 hr | `#support-escalations` |

Unlisted reasons (`system_error`, `classifier_error`, …) fall back to the P3
default route. Auto-refunds are capped at `AUTO_REFUND_LIMIT_INR` (₹2000);
order cancellations are never automated. Slack alerts fire immediately for P1
escalations and at 75% of any SLA window via the watchdog.
