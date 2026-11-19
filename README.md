# Outreach Desk

An evidence-based outbound system. Give it company domains (or let it find recent Series A–C raises in
funding news) and for each one it:

1. **Researches** the company: free sources first (news, job boards, Wikidata), its own pages last,
   within hard caps per company.
2. **Qualifies** it against your ideal-customer gates (`config/icp.yaml`). Every gate is settled by a
   quoted, linked source; anything it can't prove goes to *needs review*, never to *qualified*.
3. **Finds the person**: Hunter search, title ranking and email verification.
4. **Drafts the email** from one cited hook and two role-specific pains.
5. **Sends the draft to your own inbox only** (the code refuses any other address), then runs a
   **reply agent** (LangGraph) that reads replies, offers times from your calendar, books, logs to
   HubSpot, or hands off to you.
6. **Learns**: ratings, edits and reply outcomes become lessons; only lessons you approve shape new drafts.

Python standard-library HTTP server + one self-contained HTML page. Runs locally or on Vercel.

## Deploy to Vercel (5 minutes)

1. Push this folder to a new GitHub repository (see *Push to GitHub* below).
2. In Vercel: **Add New → Project → Import** the repository. Framework preset: **Other**. No build
   command. Deploy.
3. Open the URL. With no database configured it starts as a **demo workspace** with fictional data,
   so you can check everything works.
4. To use it for real, add these under **Project → Settings → Environment Variables**, then redeploy:

| Variable | Needed for |
|---|---|
| `APP_PASSWORD` | Signing in. Without it (and without `OPEN_ACCESS=1` / `DEMO_MODE=1`) a deployment stays locked |
| `APP_SECRET` | Signs session cookies. Any long random string (`openssl rand -hex 24`) |
| `DATABASE_URL` | Keeping data. Postgres connection string (Supabase: use the pooler URL). Apply `db/schema_pg.sql` once |
| `GEMINI_API_KEY` or `ANTHROPIC_API_KEY` | Research, drafting, reply classification. Whichever is set is used |
| `HUNTER_API_KEY` | Finding and verifying contacts |
| `OWN_EMAIL` | The only address the system may send to |
| `GOOGLE_TOKEN_JSON` | Gmail send/read and Calendar. Create with `python -m src.google_setup` locally, paste the JSON |
| `HUBSPOT_TOKEN` | Optional. Logs outcomes to HubSpot |
| `CRON_SECRET` | Optional. Lets the scheduled job check replies |
| `APP_NAME`, `REPO_URL`, `REPORT_URL` | Optional branding and sidebar links |

The **Settings** screen in the app shows which of these are connected. `GET /api/health` returns the
same as booleans (never secrets).

Once `DATABASE_URL` is set, demo mode switches off automatically. Force it with `DEMO_MODE=1` or `0`.

### Scheduled reply checks

`.github/workflows/reply-agent.yml` calls the app every 15 minutes. In GitHub → Settings → Secrets →
Actions, add `APP_URL` (your `https://…vercel.app`) and `CRON_SECRET` (same value as in Vercel).

## Push to GitHub

```bash
cd outreach-desk
git init -b main
git add .
git commit -m "Outreach Desk"
gh repo create outreach-desk --private --source=. --push
# or: create an empty repo on github.com, then
# git remote add origin https://github.com/<you>/outreach-desk.git && git push -u origin main
```

CI (`.github/workflows/ci.yml`) runs the test suite on every push.

## Run locally

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
cp .env.example .env            # fill in what you have
.venv/bin/python -m src.webapp  # http://127.0.0.1:8765
```

Try it with sample data first: `DEMO_MODE=1 SQLITE_PATH=/tmp/demo.sqlite .venv/bin/python -m src.webapp`.
Locally it answers on 127.0.0.1 only and uses SQLite (`db/outreach.sqlite`) unless `DATABASE_URL` is set.

Command-line pipeline (no web app):

```bash
.venv/bin/python -m src.pipeline --domains data/sample_domains.txt --dry-run
```

## Tests

```bash
.venv/bin/python -m pytest -q
```

## Make it yours

- **Who you sell for, the gates, weights, caps, title ranking, value props:** `config/icp.yaml`.
  The `decisions` block records why each value was chosen.
- **Customers to skip:** replace `data/hubspot_customers.csv` with your HubSpot export.
- **What settles each variable:** `outputs/evidence_map.md`. Every module's inputs and failure modes:
  `outputs/components_map.md`.

## Guarantees and where they're enforced

| Rule | Where |
|---|---|
| Any failed gate means *not a fit* and research stops | `qualify.decide`, `agent_loop.run` |
| An unanswered gate means *needs review*, never *qualified* | `qualify.decide`, `config.validate_icp` |
| Research can't run without caps and stops at them | `AgentLoop`, `CallBudget` |
| The planner can only choose whitelisted actions; code builds every URL | `agent_loop._plan` |
| Facts whose quote isn't found in the source are dropped | `research/validate.py` |
| Hunter: no domain searched twice; monthly credit cap enforced | `poc_hunter.py` |
| Email goes only to `OWN_EMAIL` (checked twice) | `send_gmail.check_recipient` |
| robots.txt respected; CAPTCHAs and logins never bypassed | `research/fetch.py` |
| Actions needing a missing integration fail with a fixable message (HTTP 503) | `server.require` |
| Security headers, CSP, 1 MB body limit, login rate limit, HttpOnly signed session | `server.Handler` |
