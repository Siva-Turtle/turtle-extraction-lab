# Turtle Extraction Lab — agent testing app

Prototyping ground for Turtle's extraction agents. Agents are defined, tested against
sample transcriptions / messages / mails via OpenRouter models, rated per-attribute,
and logged. Once finalised, agent configs move to the **Extraction Service**
(`05 - Integrations/Extraction Service.md` in the PRD vault).

## The 3 repos

| Repo | GitHub | Local path |
|---|---|---|
| PRD vault (spec, wins on behaviour) | `Siva-Turtle/turtle-memory-prd` | `C:\SivaAdithya\Turtle Memory` |
| CRM prototype (wins on engineering) | `Siva-Turtle/turtle-crm` | `C:\Users\sivaa\OneDrive\SivaAdithya\Coding\VSCode\turtle\turtle-crm` |
| This lab | `Siva-Turtle/turtle-extraction-lab` | `C:\Users\sivaa\OneDrive\SivaAdithya\Coding\VSCode\turtle\turtle-extraction-lab` |

## Layout

```
turtle-extraction-lab/
  CLAUDE.md / AGENTS.md   agent orientation (read first)
  start-lab.cmd           double-click to start everything (backend + frontend)
  backend/                FastAPI 3.13 + SQLAlchemy 2.0 sync + psycopg, db turtle_agent_lab
  backend/alembic/        migrations (0001_initial: agents, attributes, runs, feedbacks, run_logs)
  backend/tests/          pytest suite (SQLite-backed, no Postgres needed)
  frontend/               Vite + React 18 + TS, Tailwind 3.4 with CRM v2 tokens, :5175 → :8002
  scripts/dev.ps1         starts backend + frontend (what start-lab.cmd calls)
  scripts/db-init.ps1     first-time setup: create DB, write .env, migrate, seed
```

Ports are offset from the CRM on purpose: CRM uses 8000/5173 (test stack 8001/5174),
this lab uses **8002/5175** so all three run side by side.

## Modules (high level)

- **Config:** Agents (system instruction + prompt) → Attributes (type, description,
  structured-output schema, mapped to one agent).
- **Test:** pick input type (`transcription` | `messages` | `mail`) + data, select agents,
  pick OpenRouter model → per-agent per-attribute output with `value, confidence,
  confidence_type (quoted|inferred|…), evidence (exact quote)` → thumbs up/down + remarks.
- **Log:** one denormalized row per run — full agent/attribute/model snapshots + user
  feedback. **No FK to agents/attributes** (configs change later; the log must stay frozen).

## First-time setup (one command)

Double-click **`db-init.cmd`** — or from PowerShell 7:

```powershell
./scripts/db-init.ps1
```

(Windows opens a double-clicked `.ps1` in Notepad by design, so use the
`.cmd` wrapper — it hands `scripts\db-init.ps1` over to `pwsh`, same as
`start-lab.cmd` does for `scripts\dev.ps1`.)

It prompts securely for the postgres password (same `postgres` user on
`localhost:5432` as turtle-crm, so enter the SAME password), verifies it via
`psql`, then creates database `turtle_agent_lab`, writes `backend/.env`
(without ever echoing the password), runs `alembic upgrade head`, and seeds
one demo agent ("Contact Facts" + 3 attributes). Afterwards set
`OPENROUTER_API_KEY` in `backend/.env`.

## Running

Double-click **`start-lab.cmd`** — or from PowerShell:

```powershell
./scripts/dev.ps1
```

This checks PostgreSQL on `localhost:5432`, refuses to start if port 8002 or 5175 is
already held, starts the backend + frontend together with `[api]` / `[web]` output,
and opens http://localhost:5175 once Vite answers. Ctrl+C stops both.
Health check: `GET http://localhost:8002/api/v1/health` → `{"status":"ok"}`.

Manual path (from `backend/` / `frontend/`):

```powershell
cd backend
uv sync
uv run alembic upgrade head
uv run python -m app.seed
uv run uvicorn app.main:app --reload --port 8002
```

```powershell
cd frontend
npm install
npm run dev
```

## Auto Select Agents

`POST /runs/auto` runs the Agent Identifier (12 fixed yes/no questions about
the CLIENT's own situation) then deterministic routing (`plan_auto_agents`):

- `has_assets` → `asset`, `has_accounts` → `account`, `expenses` → `expense`,
  `goals` → `goal`, `income` → `income`, `liabilities` → `liability` (all attrs).
- `credit_cards` → `basic_info` Banking, `employment_changed` /
  `employment_status_changed` → Employment,
  `alumni` → Education / Alumni (subset, DB order).
- `insurance` → `insurance` (all attrs); `tax` → `tax` (all attrs);
  both → `tax_and_insurance` (all attrs). Fallback to the combined subset
  when the split agent is missing/disabled; both splits (insurance then tax)
  when the combined agent is missing/disabled.
- Always-run (unscored): `behavioral`, `query`, `feedback` (all attrs).
  Karma Conversation meetings run `kc_and_feedback` full instead of `feedback`.
  (`kc` exists for manual runs only and is never auto-selected.)
- Meeting-type rules (case-insensitive on `meeting_type`): Karma Conversation
  runs `kc_and_feedback` full + `basic_info` full; Kick-off (`kick-off` /
  `kickoff` / `kick off`) runs `basic_info` full.

Consistency v2 is agent-level (`hit` / `miss` / `not_scored` / `error`,
score = hits / (hits + misses)); misses get an auto thumbs-down on the agent
(`feedback[agent]["__agent__"]`). Old logs (no `version`) keep the old rendering.

## Attributes v2

Version 2 (rev 2, approved 2026-10-03) replaces the 7 group-wise attributes
with one list attribute per group: `assets` (Assets → asset), `accounts`
(Banking / Accounts → account), `expenses` (Expenses → expense), `goals`
(Goals → goal), `income_sources` (Income → income), `liabilities`
(Liabilities → liability), `insurance_policies` (Insurance →
tax_and_insurance + insurance). Tax, Banking (credit_cards) and all other
groups are untouched; the 2 adequacy booleans are kept.

- `wrap_result=False` on the 7 lists: the extraction output returns the JSON
  array directly (no `{value, confidence, confidence_type, evidence}`
  wrapper); confidence lives per item. Wrapped attributes are unchanged.
- Amount convention: currency code + space + digits only (`INR 1000000`,
  `USD 5400`); `0` = none of this type, `Yes` = has it but no figure,
  `null` = not mentioned.
- Backup (restore source): `exports/attributes_v1_backup_2026-10-03.json`
  (via `scripts/backup_attributes_v1.py`). Migration `0017_attributes_v2`
  deletes 77 / inserts 7 on the current DB.
- Downgrade: `alembic downgrade -1` restores the 77 deleted v1 rows (same
  ids, from `0017_attributes_v1_deleted.json`) and drops `wrap_result`.

## Tests

From `backend/`:

```powershell
uv run pytest -q
```

The suite runs against in-memory SQLite (no Postgres needed) with OpenRouter stubbed,
covering agent/attribute CRUD, runs, per-attribute feedback, and log snapshots.

## Design

Mirrors the CRM prototype's v2 Turtle theme exactly (`frontend/src/index.css`,
`tailwind.config.ts`, `src/app/`, `src/components/ui/` ported from turtle-crm):

- Paper cards: `rounded-2xl`, `#e5e7eb` border, float shadow; ink-bar pill buttons
  (mint in dark mode); mint accent bar + `font-module` (Plus Jakarta Sans) page headers.
- App shell: collapsible sidebar (mint active state), 58px top bar, `#eef0f4` page
  background, bottom tab bar on mobile.
- Real Turtle wordmark (`public/turtle-logo-black.svg` / `turtle-logo-white.svg`,
  swapped by theme) + `turtle-favicon.svg`; light/dark toggle in the top bar
  (persisted, pre-paint script prevents flashing).
