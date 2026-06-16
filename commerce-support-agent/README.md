# Commerce Support AI Agent

> **Production-ready AI customer support for Medusa.js stores** — automatically classifies and replies to Zoho Desk tickets using a two-pass Claude architecture, with a quality gate, human escalation routing, Supabase audit log, and SLA watchdog.

![Python](https://img.shields.io/badge/Python-3.11+-blue?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-0.111-009688?logo=fastapi&logoColor=white)
![Claude](https://img.shields.io/badge/Claude-Sonnet-blueviolet?logo=anthropic&logoColor=white)
![Zoho Desk](https://img.shields.io/badge/Zoho_Desk-integrated-E42527?logo=zoho&logoColor=white)
![Medusa](https://img.shields.io/badge/Medusa.js-v2-black?logo=medusa&logoColor=white)
![Supabase](https://img.shields.io/badge/Supabase-integrated-3ECF8E?logo=supabase&logoColor=white)
![CI](https://img.shields.io/badge/Tests-9%2F9_green-brightgreen)

---

## What it does

When a customer opens a support ticket in Zoho Desk, this agent:

1. **Fetches** the ticket + full conversation thread from Zoho Desk
2. **Enriches** it with live order data from Medusa (by order # or customer email)
3. **Classifies** the intent with Claude Sonnet (Pass 1 → structured JSON)
4. **Escalates immediately** for fraud, legal threats, large refunds, low confidence, or PII leaks — routing to the right team with a handoff brief + customer acknowledgement
5. **Executes commerce actions** — auto-issues refunds ≤ ₹2000 directly in Medusa
6. **Generates a brand-voiced reply** with Claude Sonnet (Pass 2)
7. **Quality-gates the draft** (PII regex, length, overpromise detection) before sending
8. **Posts the reply** back to Zoho Desk and logs to Supabase

A background SLA watchdog warns on Slack at 75% of each SLA window. Every unhandled exception becomes a human escalation — no ticket is ever silently dropped.

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
  6. commerce action        refund ≤ ₹2000 → issue_refund (Medusa 2-step)
                            refund > limit / cancel → escalate ──► STOP
  7. generate_reply()       Claude Sonnet  (PASS 2 → email body)
  8. quality_gate           PII / length / policy check
                            fail → escalate (with draft) ──► STOP
  9. post_reply() + log     Zoho Desk + Supabase
       │
       ▼
APScheduler: sla_watchdog() every 15 min → Slack warning at 75% SLA
```

---

## Tech stack

| Layer | Technology |
|---|---|
| API framework | FastAPI + Uvicorn |
| AI | Claude Sonnet (two-pass: classify → reply) |
| Helpdesk | Zoho Desk (OAuth Self-Client) |
| Commerce | Medusa.js v2 (Admin API) |
| Database / audit | Supabase (PostgreSQL) |
| HTTP client | httpx + tenacity (retry) |
| Scheduling | APScheduler |
| Alerts | Slack Incoming Webhooks |
| Containerisation | Docker |

---

## Project layout

```
commerce-support-agent/
├── app/
│   ├── main.py            FastAPI app — webhook endpoint, health, lifespan
│   ├── agent.py           run_agent() — 9-step orchestrator
│   ├── medusa.py          Medusa v2 Admin API client
│   ├── zohodesk.py        Zoho Desk client (OAuth token manager)
│   ├── escalation.py      handoff brief, customer ack, Slack, Supabase log
│   ├── quality_gate.py    PII / length / overpromise checks
│   ├── brand_config.py    per-client brand voice + policy
│   ├── watchdog.py        SLA watchdog (APScheduler)
│   └── config.py          env loading, team routing, priority map, constants
├── prompts/
│   ├── classifier.py      Pass 1 — classify_ticket() → structured JSON
│   └── reply_generator.py Pass 2 — generate_reply() → email body
├── tests/
│   ├── test_e2e.py        9 mocked pipeline tests (CI-runnable, no creds)
│   ├── test_classifier.py 8 intent fixtures (integration — live Claude)
│   └── test_clients.py    Medusa / Zoho smoke tests (integration)
├── scripts/
│   └── seed_supabase.sql  DDL for escalations + processed_tickets tables
├── docker-compose.medusa.yml  local Postgres + Redis for Medusa dev
├── Dockerfile             python:3.11-slim image
├── SETUP_GUIDE.md         full account setup walkthrough
└── .env.example           all required environment variables
```

---

## Quick start

### Prerequisites

- Python 3.11+
- A [Zoho Desk](https://desk.zoho.com) account with a configured Self-Client OAuth app
- A running [Medusa.js v2](https://medusajs.com) backend with a secret API key
- A [Supabase](https://supabase.com) project with the schema from `scripts/seed_supabase.sql`
- An [Anthropic API key](https://console.anthropic.com) (or the `claude` CLI logged in — see [Claude auth](#claude-authentication))

### Install

```bash
git clone https://github.com/ShubhamChougale01/commerce-support-agent.git
cd commerce-support-agent
pip install -r requirements.txt
cp .env.example .env   # fill in your values
```

### Run

```bash
uvicorn app.main:app --reload --port 8001
curl localhost:8001/health   # → {"status":"ok"}
```

> **Port note:** the Medusa storefront occupies port 8000. The support agent
> defaults to **8001**. Set `PORT=8001` in `.env` (already the default in
> `.env.example`). The full local stack: Medusa backend → 9000, storefront → 8000,
> support agent → 8001.

### Run with Docker

```bash
docker build -t support-agent .
docker run --rm -p 8001:8001 --env-file .env support-agent
```

The container honours `$PORT`, so it deploys to Railway or Render with no changes.

---

## Environment variables

| Variable | Required | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | Recommended | Claude API key. If blank, falls back to `claude-agent-sdk` (dev only — see below). |
| `ZOHO_CLIENT_ID` | Yes | Zoho OAuth Self-Client ID |
| `ZOHO_CLIENT_SECRET` | Yes | Zoho OAuth Self-Client secret |
| `ZOHO_REFRESH_TOKEN` | Yes | OAuth refresh token |
| `ZOHO_ORG_ID` | Yes | Zoho Desk organisation ID (not department ID) |
| `ZOHO_DC` | Yes | Data centre: `com`, `in`, `eu`, `com.au` |
| `ZOHO_FROM_EMAIL` | Yes | Verified Desk reply-from address |
| `ZOHO_TEAM_FRAUD` | Yes | Team ID for fraud escalations |
| `ZOHO_TEAM_LEGAL` | Yes | Team ID for legal escalations |
| `ZOHO_TEAM_REFUNDS` | Yes | Team ID for refund review escalations |
| `ZOHO_TEAM_GENERAL` | Yes | Team ID for general escalations |
| `MEDUSA_URL` | Yes | Medusa backend base URL |
| `MEDUSA_API_KEY` | Yes | Medusa secret API key |
| `SUPABASE_URL` | Yes | Supabase project URL |
| `SUPABASE_KEY` | Yes | Supabase `service_role` key |
| `DESK_WEBHOOK_SECRET` | Yes | Shared secret for webhook signature verification |
| `SLACK_WEBHOOK_URL` | Optional | Incoming webhook URL — enables P1 alerts and SLA warnings |
| `PORT` | Optional | Port the agent listens on (default `8001`; 8000 is the Medusa storefront) |

See `SETUP_GUIDE.md` for step-by-step account configuration.

---

## Claude authentication

`prompts/_llm_client.py` picks the Claude backend automatically:

- **`ANTHROPIC_API_KEY` set** → standard `anthropic.AsyncAnthropic` (production).
- **`ANTHROPIC_API_KEY` blank** → `AgentSDKAnthropic` shim over `claude-agent-sdk` using the logged-in `claude` CLI — no API key required for local dev.

```bash
pip install claude-agent-sdk
claude login   # one-time browser auth
```

> The subscription path is for development only. Anthropic does not approve subscription auth for automated backend services — set a real API key for production.

---

## Tests

```bash
# Fast, fully mocked — safe for CI:
pytest -m "not integration"

# Live API tests (requires real .env credentials):
export TEST_EMAIL=customer@example.com
export TEST_TICKET_ID=123456789
pytest -m integration
```

---

## Escalation routing

`app/config.py` maps each escalation reason to a Zoho Desk team, priority, SLA, and Slack channel:

| Reason | Priority | SLA | Slack channel |
|---|---|---|---|
| `fraud_flag` | P1 — High | 1 hr | `#fraud-alerts` |
| `legal_threat` | P1 — High | 2 hr | `#legal-escalations` |
| `refund_above_threshold` | P2 — High | 4 hr | `#refund-review` |
| `abusive_language`, `quality_gate_fail`, `low_confidence`, `human_requested` | P3 — Medium | 8 hr | `#support-escalations` |
| `system_error`, `classifier_error`, … | P3 — Medium (fallback) | 8 hr | `#support-escalations` |

Auto-refunds are capped at ₹2,000. Order cancellations are never automated. Slack alerts fire immediately for P1 and at 75% of any SLA window via the watchdog.

---

## Zoho Desk webhook setup

1. **Setup → Automation → Workflows → Create Rule** on module *Tickets*, trigger *Create*.
2. Action: **Webhook** → `POST https://<your-host>/webhook/desk`
3. Custom header: `X-Webhook-Secret: <value from .env>`
4. Body (JSON): `{"ticket_id": "${ticketId}"}`

The endpoint verifies the secret using constant-time comparison, checks idempotency in Supabase, and processes the ticket in a background task.
