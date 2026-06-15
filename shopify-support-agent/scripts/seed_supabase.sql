-- Schema for the Shopify Customer Support AI Agent.
-- Run against your Supabase Postgres instance (SQL editor or psql).

-- --------------------------------------------------------------------------
-- escalations
--   Audit + SLA-tracking row written every time a ticket is handed to a human.
-- --------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS escalations (
    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    ticket_id            TEXT        NOT NULL,
    client_id            TEXT        NOT NULL,
    customer_email       TEXT,
    escalation_reason    TEXT        NOT NULL,
    confidence           FLOAT,
    fraud_flags          JSONB,
    refund_amount        INTEGER,
    agent_group          TEXT,  -- Zoho Desk team ID (string)
    priority             INTEGER,
    sla_hrs              INTEGER,
    handoff_brief        TEXT,
    customer_ack_sent_at TIMESTAMPTZ,
    escalated_at         TIMESTAMPTZ DEFAULT now(),
    slack_warned         BOOLEAN     DEFAULT FALSE,
    resolved_by          TEXT,
    resolved_at          TIMESTAMPTZ,
    resolution_action    TEXT,
    human_edited_draft   BOOLEAN     DEFAULT FALSE
);

-- Supports the watchdog query (open escalations, ordered by age).
CREATE INDEX IF NOT EXISTS idx_escalations_unresolved
    ON escalations (resolved_at, escalated_at)
    WHERE resolved_at IS NULL;

-- --------------------------------------------------------------------------
-- processed_tickets
--   Idempotency guard. The webhook inserts a row before dispatching work; a
--   repeat delivery of the same ticket_id short-circuits to 200.
-- --------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS processed_tickets (
    ticket_id    TEXT PRIMARY KEY,
    processed_at TIMESTAMPTZ DEFAULT now(),
    client_id    TEXT
);

-- --------------------------------------------------------------------------
-- tickets_processed
--   Analytics log written in run_agent step 9 after a reply is auto-sent.
--   (Distinct from processed_tickets, which is the idempotency guard.)
-- --------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS tickets_processed (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    ticket_id    TEXT        NOT NULL,
    client_id    TEXT,
    intent       TEXT,
    confidence   FLOAT,
    action_taken TEXT,
    tokens_used  INTEGER,
    processed_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_tickets_processed_ticket
    ON tickets_processed (ticket_id);
