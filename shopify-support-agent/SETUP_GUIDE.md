# Account Setup Guide — fill `.env` as you go

Work through these in order. Each section ends with exactly which `.env`
line(s) it fills. Total time: ~60-75 min (Zoho OAuth is the fiddly one).

---

## 1. Zoho Desk (~25 min — includes OAuth)

### 1a. Create the Desk portal
1. Go to **https://www.zoho.com/desk/** → Sign Up Free (personal email OK,
   no card). Free plan supports 3 agents.
2. Note which domain you signed up on: zoho.**com** vs zoho.**in** —
   that's your **ZOHO_DC** (`com` or `in`).
3. Find your org ID: **Setup (gear) → Developer Space → API** →
   "OrgId" is shown there → **ZOHO_ORG_ID**.

### 1b. Create the 4 routing teams
1. **Setup → Organization → Teams → New Team**
2. Create: `Fraud Review`, `Legal`, `Refunds`, `General Support`
   (add yourself to each).
3. Open each team and copy the long numeric ID from the URL →
   **ZOHO_TEAM_FRAUD / ZOHO_TEAM_LEGAL / ZOHO_TEAM_REFUNDS / ZOHO_TEAM_GENERAL**

### 1c. OAuth Self Client (gives the agent API access)
1. Go to **https://api-console.zoho.com** (`.in` if your account is on the
   India DC) → **Add Client → Self Client → Create**.
2. Copy **Client ID** → **ZOHO_CLIENT_ID** and **Client Secret** →
   **ZOHO_CLIENT_SECRET**.
3. In the **Generate Code** tab:
   - Scope: `Desk.tickets.ALL,Desk.contacts.READ,Desk.basic.READ,Desk.settings.READ`
   - Time duration: 10 minutes
   - Description: anything → **Create** → copy the generated code.
4. Exchange the code for a refresh token within 10 minutes — run
   (replace the three values):
   ```bash
   curl -X POST "https://accounts.zoho.com/oauth/v2/token" \
     -d "grant_type=authorization_code" \
     -d "client_id=YOUR_CLIENT_ID" \
     -d "client_secret=YOUR_CLIENT_SECRET" \
     -d "code=PASTED_CODE"
   ```
   (use `accounts.zoho.in` if ZOHO_DC=in)
5. The response contains `"refresh_token": "1000.xxxx..."` →
   **ZOHO_REFRESH_TOKEN**. (The `access_token` in the same response can be
   ignored — the app refreshes its own.)
6. **ZOHO_FROM_EMAIL**: Setup → Channels → Email → your support address
   (the default is `support@<portal>.zohodesk.com` — that works).

Fills:
```
ZOHO_CLIENT_ID=  ZOHO_CLIENT_SECRET=  ZOHO_REFRESH_TOKEN=
ZOHO_ORG_ID=     ZOHO_DC=             ZOHO_FROM_EMAIL=
ZOHO_TEAM_FRAUD= ZOHO_TEAM_LEGAL=  ZOHO_TEAM_REFUNDS=  ZOHO_TEAM_GENERAL=
```

---

## 2. Medusa.js local store (~20 min)

Prereqs: Docker Desktop + Node 20+.

1. Start the backing services:
   ```bash
   cd shopify-support-agent
   docker compose -f docker-compose.medusa.yml up -d
   ```
2. Scaffold the store (one-time):
   ```bash
   npx create-medusa-app@latest medusa-store \
     --db-url postgres://medusa:medusa@localhost:5433/medusa
   ```
   Say **yes** to the admin dashboard, **skip** the Next.js storefront.
   Create the admin user when prompted.
3. Run it:
   ```bash
   cd medusa-store && npm run dev
   ```
   Admin UI: **http://localhost:9000/app**
4. Create the API key: **Settings → API Key Management → Create API Key**
   → type **Secret** → copy → **MEDUSA_API_KEY**.
5. Seed test data in the admin UI:
   - 3 products: ~₹500, ~₹800, ~₹3500 (set INR as store currency in
     Settings → Store if needed)
   - 2 orders (Orders → Create draft order → add product → set customer
     email → complete + mark paid):
     - Order A: email YOU control (this is **TEST_EMAIL**)
     - Order B: a different email (fraud-mismatch test)
   - Note both order display numbers (#1, #2, ...).

Fills:
```
MEDUSA_URL=http://localhost:9000
MEDUSA_API_KEY=sk_...
TEST_EMAIL=
```

---

## 3. Supabase (~5 min — project already exists)

The project is already created (ref `kybrkxbamzzqgkfxabzd`) and the MCP
server is connected, so Claude Code can run the schema itself.

1. Get the credentials: **Project Settings (gear) → API**
   - **Project URL** → **SUPABASE_URL**
   - **service_role key** (NOT anon) → **SUPABASE_KEY**
2. Schema: ask Claude Code to "run the seed SQL via MCP" — or paste
   `scripts/seed_supabase.sql` into the SQL Editor yourself.

Fills:
```
SUPABASE_URL=https://kybrkxbamzzqgkfxabzd.supabase.co
SUPABASE_KEY=eyJ...   (service_role)
```

---

## 4. Slack incoming webhook (~5 min)

1. **https://api.slack.com/apps** → **Create New App → From scratch**
   → name `Support Agent Alerts`, pick your workspace.
2. **Incoming Webhooks** → toggle **On** → **Add New Webhook to Workspace**
   → choose a channel → copy the URL → **SLACK_WEBHOOK_URL**

---

## 5. Anthropic API key (~2 min)

**https://console.anthropic.com** → API Keys → Create Key →
**ANTHROPIC_API_KEY**

---

## Done? Final checklist

- [ ] `.env` fully filled (DESK_WEBHOOK_SECRET is pre-generated — leave it)
- [ ] 4 Zoho team IDs in .env
- [ ] Medusa running on :9000 with 3 products + 2 orders
- [ ] Supabase schema applied (3 tables)
- [ ] A test ticket created in Zoho Desk (subject like
      "Where is my order #1?") — note its ticket ID → **TEST_TICKET_ID**

Then tell Claude Code: **"accounts ready"** and paste:
1. a Medusa order display number + its customer email,
2. the Zoho test ticket ID.

Claude will then: run live smoke tests → run the 8 classifier fixtures and
tune to 7/8 → walk flows 1-5 → set up the Zoho workflow webhook with you.
