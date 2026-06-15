# Commerce Support AI Agent — project context

## What this is
Production-ready AI customer-support agent: **Zoho Desk** tickets for a
**Medusa.js** store, resolved by a **two-pass Claude architecture**
(Pass 1: classify → JSON; Pass 2: brand-voiced reply), with a quality gate,
human escalation routing, Supabase audit log, and an SLA watchdog.

> Folder is named `shopify-support-agent/` for historical reasons — v1
> targeted Shopify + Freshdesk; both were swapped out behind unchanged
> internal interfaces (adapter pattern). Don't rename without asking.

## Repo layout
```
proj/
├── CLAUDE.md                  ← this file
├── .mcp.json                  ← Supabase MCP (NOTE: tools never load in this
│                                harness session — use supabase-py via .env
│                                creds instead; that works and is verified)
├── plan/                      ← gitignored working docs
│   ├── PLAN.md                original 11-phase build plan (all done)
│   ├── PROGRESS.md            phase-by-phase checklist w/ verification notes
│   └── MIGRATION_PLAN.md      Freshdesk→Zoho / Shopify→Medusa (M1-M7, done)
└── shopify-support-agent/     ← the application
    ├── app/
    │   ├── main.py            FastAPI; POST /webhook/desk (X-Webhook-Secret,
    │   │                      constant-time), idempotency, lifespan watchdog
    │   ├── agent.py           run_agent() — 9-step orchestrator
    │   ├── zohodesk.py        Zoho client; OAuth token manager (cache→refresh
    │   │                      →retry-on-401); normalizes ticket/thread shapes
    │   ├── medusa.py          Medusa v2 client; normalizes orders; 2-step
    │   │                      refund (find captured payment → refund)
    │   ├── escalation.py      escalate_to_human: gather(brief,ack) →
    │   │                      patch(teamId+priority str) → note → reply →
    │   │                      Supabase log → Slack if P1
    │   ├── quality_gate.py    6 PII regexes + length(300w) + overpromise
    │   ├── watchdog.py        APScheduler 15-min; warn at 75% SLA
    │   ├── brand_config.py    BrandConfig dataclass; default for all clients
    │   └── config.py          load_dotenv() HERE ONLY; ROUTING (team_id,
    │                          priority int → PRIORITY_MAP str); get_route()
    │                          fallback; AUTO_REFUND_LIMIT_INR=2000
    ├── prompts/
    │   ├── classifier.py      Pass 1 — Sonnet, max_tokens=512, JSON-only,
    │   │                      retry-once, fallback escalation dict
    │   └── reply_generator.py Pass 2 — Sonnet, max_tokens=800, brand prompt
    ├── tests/                 pytest.ini: asyncio_mode=auto, marker integration
    │   ├── test_e2e.py        9 CI tests, all external calls mocked — GREEN
    │   ├── test_classifier.py 8 fixtures, live Claude (integration)
    │   └── test_clients.py    live Medusa/Zoho smoke (integration; skip w/o
    │                          TEST_EMAIL / TEST_TICKET_ID)
    ├── scripts/seed_supabase.sql   3 tables (applied to live DB ✅)
    ├── docker-compose.medusa.yml   local PG:5433 + Redis:6380 + setup notes
    ├── Dockerfile             python:3.11-slim; image `support-agent` builds+runs
    ├── SETUP_GUIDE.md         account walkthrough (Zoho OAuth, Medusa, etc.)
    └── BLOCKERS.md            live go-live checklist — CHECK THIS FOR STATUS
```

## Internal data shapes (the contract everything depends on)
Clients normalize INTO these; business logic never sees raw API responses:
- Ticket: `{id, subject, requester:{email,name}, description_text}`
- Order: `{order_number, name, email, total_price, fulfillment_status,
  fulfillments:[{shipment_status, tracking_numbers, tracking_urls,
  estimated_delivery_at}]}`
- Classifier keys `shopify_action` / `requires_shopify_action` are KEPT as
  internal vocabulary post-migration (decision D3) — don't rename casually.

## Commands
```bash
cd shopify-support-agent
pytest -m "not integration"      # CI suite — 9 tests, must stay green
pytest -m integration            # live APIs; needs full .env
uvicorn app.main:app --reload    # local run; GET /health
docker build -t support-agent .  # verified working
```
Windows console: prefix Python runs with `PYTHONIOENCODING=utf-8` when output
may contain ₹ (cp1252 console chokes otherwise).

## Key decisions (full rationale in plan/MIGRATION_PLAN.md D1-D5)
- Zoho auth = Self-Client OAuth; access token cached, auto-refreshed.
- Webhook auth = shared-secret header (Zoho can't HMAC-sign), compare_digest.
- Tags dropped on escalation (Zoho PATCH has no tags); reason lives in the
  private note + Supabase row.
- Medusa v2; amounts assumed MAJOR units (verify during live Flow 2).
- Never auto-cancel orders; auto-refund capped at ₹2000.
- Any unhandled run_agent exception → escalate_to_human("system_error").
- Idempotency-store-down → webhook returns 503 (fail closed, desk retries).

## Current status (2026-06-14)
- Build phases 1-11: ✅ all done (plan/PROGRESS.md)
- Migration M1-M7: ✅ all done (plan/MIGRATION_PLAN.md)
- Supabase: schema applied + DB verified end-to-end with app query patterns ✅
- Local Medusa: ✅ running (admin admin@test.com / supersecret123), INR region +
  3 test products + 2 orders (#2 captured+shipped w/ tracking; #3 stranger email)
- Zoho Desk: ✅ live — OAuth, all 4 real team IDs, test tickets #101-103+ created
- ALL 5 FLOWS VERIFIED LIVE ✅ (order-status reply, ₹800 auto-refund, ₹3500
  escalation, email-mismatch fraud P1, duplicate-webhook idempotency)
- Classifier accuracy: 7/8 fixtures (meets ≥7/8; legal_threat is a label nuance
  — model says refund_request not complaint, but still escalates correctly)
- PENDING: SLACK_WEBHOOK_URL (only blocks the P1 Slack ping; escalation works
  without it) → then deploy phase (git init → Railway + public Medusa → Zoho
  webhook → prod ticket test). See BLOCKERS.md.
- Known TODOs: tokens_used logged as None; brand config Supabase lookup stubbed;
  COMPETITOR_BRANDS empty.

### Claude auth (IMPORTANT — no API key in use)
ANTHROPIC_API_KEY is intentionally blank. The app runs Claude via the Claude
Code SUBSCRIPTION through `prompts/_llm_client.py::get_client()`: if the key is
set it uses the real `anthropic.AsyncAnthropic`; if blank it uses
`AgentSDKAnthropic`, a shim over `claude-agent-sdk` (the logged-in `claude` CLI).
classifier.py / reply_generator.py / escalation.py all call get_client(). For
production, set a real ANTHROPIC_API_KEY (subscription auth for an automated
backend is not an approved Anthropic use case). Needs `pip install
claude-agent-sdk` + a logged-in CLI.

## Gotchas learned the hard way
- `os.getenv(name, default)` returns "" for set-but-empty .env vars — use
  `os.getenv(name) or default` (bit us on ZOHO_TEAM_* routing).
- .env is gitignored and holds a pre-generated DESK_WEBHOOK_SECRET — preserve
  it when rewriting the file.
- This harness doesn't load project .mcp.json MCP tools mid-session or even
  after restart — Supabase access goes through supabase-py instead.
- Verify each phase with mocked orchestration before claiming done; e2e tests
  monkeypatch at the `app.agent` module level (names are re-exported there).
- Medusa v2 admin `/orders` SILENTLY IGNORES a `display_id` filter param (returns
  ALL orders) — get_order_by_id pages newest-first and matches client-side.
- ZOHO_ORG_ID must be the ORG id (from GET /organizations → id, e.g. 60073999561),
  NOT the department id. Wrong org id → 500 on /tickets and /departments.
- ZOHO_FROM_EMAIL must be a verified Desk reply address; on the .in DC that's
  `support@<portal>.zohodesk.in` (NOT zohomail.in). Wrong from → sendReply 422.
- Zoho OAuth scope is Desk.contacts.READ (read-only): can't create contacts via
  API. Workaround — POST /tickets with an inline `contact` object (tickets.ALL
  scope) auto-creates the contact.
- Fraud email-mismatch needs the requester email: classify_ticket takes
  `customer_email` and the prompt only applies the rule when it's present (no
  guessing). agent.py passes ticket requester email through.
- create-medusa-app is a monorepo (npm workspaces + turbo: apps/backend,
  apps/storefront). Run the medusa CLI as `node ../../node_modules/@medusajs/
  cli/cli.js <cmd>` from apps/backend (no .bin shim).
- The medusa migrate post-step `migrate-product-shipping-profile` errors with
  MODULE_NOT_FOUND on a fresh empty DB — harmless; schema migration still applies.
