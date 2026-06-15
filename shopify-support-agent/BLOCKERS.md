# Blockers / go-live gaps

Code is complete and CI-green. As of 2026-06-14 the agent is **verified live
end-to-end** (all 5 flows) against local Medusa + live Zoho Desk + Supabase,
running Claude on the Claude Code subscription (no API key — see CLAUDE.md
"Claude auth"). Only Slack + the deploy phase remain.

## Accounts / credentials (SETUP_GUIDE.md)
- [x] Zoho Desk portal → ZOHO_ORG_ID (60073999561), ZOHO_DC (in), ZOHO_FROM_EMAIL
      (support@agentflowintelligence.zohodesk.in)
- [x] Zoho Self Client OAuth → CLIENT_ID/SECRET/REFRESH_TOKEN
- [x] 4 Zoho teams → ZOHO_TEAM_FRAUD/LEGAL/REFUNDS/GENERAL (real IDs, verified by
      live escalation patches in Flows 3 & 4)
- [x] Medusa running locally → MEDUSA_API_KEY (secret key)
- [x] 3 test products (₹500/800/3500) + 2 orders (#2 matched+captured+shipped,
      #3 mismatched email)
- [x] Supabase URL + secret key (verified working)
- [ ] Slack incoming webhook → SLACK_WEBHOOK_URL  ← ONLY REMAINING CREDENTIAL
- [x] Claude access — via Claude Code subscription (ANTHROPIC_API_KEY blank by
      design; set a real key for production)
- [x] TEST_EMAIL (utkarshmaskar7@gmail.com) + TEST_TICKET_ID (262856000000369039)
- [x] Supabase MCP added

## Verification done (Claude Code)
- [x] seed_supabase.sql applied; DB verified programmatically
- [x] Live Medusa client smoke (get_order_by_id #2/#3, captured-payment detect,
      tracking normalization)
- [x] Live Zoho client smoke (fetch ticket, thread, sendReply)
- [x] Classifier accuracy: 7/8 fixtures (meets ≥7/8; legal_threat label nuance)
- [x] Flow 1: order-status #2 → tracking reply (sent + Supabase logged)
- [x] Flow 2: ₹800 refund → auto-refund (₹800 refund recorded on payment)
- [x] Flow 3: ₹3,500 refund → escalation (team reassign + note + ack + row)
- [x] Flow 4: email-mismatch fraud → P1 fraud team (Slack ping skipped, no URL)
- [x] Flow 5: duplicate webhook → single run_agent, 2nd returns 200
- [ ] Live watchdog test — insert past-due escalation row, confirm Slack alert
      (BLOCKED on SLACK_WEBHOOK_URL)
- [ ] Flow 4 P1 Slack ping — re-run once SLACK_WEBHOOK_URL is set

## Deploy phase (next)
- [ ] git init + push to GitHub
- [ ] Railway deploy + env vars (Medusa needs hosting too, or a tunnel for local)
- [ ] Zoho workflow rule → webhook to deployed URL (X-Webhook-Secret header)
- [ ] Production ticket test watching deploy logs

## Known code TODOs (non-blocking)
- tokens_used logged as None in tickets_processed
- get_brand_config() returns default (Supabase brands table is week 2)
- COMPETITOR_BRANDS list is empty (client-specific)
- get_order_by_id pages 100 orders and matches display_id client-side (Medusa
  ignores the display_id filter) — fine for now, paginate fully for a big store

## Bugs found & fixed during live bring-up (2026-06-14)
- get_order_by_id used a `display_id` filter Medusa ignores → wrong order returned
- ZOHO_ORG_ID held the department id, not the org id → 500s
- ZOHO_FROM_EMAIL was a zohomail.in address Zoho rejects → sendReply 422
- escalation.py used a raw Anthropic client (no key) → now uses get_client() shim
- fraud email-mismatch was inferred, not compared → classifier now takes
  customer_email and the rule only fires when it's present
