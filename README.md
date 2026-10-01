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
  backend/                FastAPI 3.13 + SQLAlchemy 2.0 sync + psycopg, db turtle_agent_lab (PG17 :5432)
  frontend/               Vite + React 18 + TS, Tailwind 3.4 with CRM v2 tokens, :5175 → :8002
  scripts/dev.ps1         starts backend + frontend
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

## First-time setup

1. Create the database:
   ```powershell
   psql -U postgres -h localhost -c "CREATE DATABASE turtle_agent_lab;"
   ```
2. Configure the backend:
   ```powershell
   Copy-Item backend/.env.example backend/.env
   ```
   Set `DATABASE_URL` password and `OPENROUTER_API_KEY`.
3. Install + run backend (from `backend/`):
   ```powershell
   uv sync
   uv run uvicorn app.main:app --reload --port 8000
   ```
   (or `./scripts/dev.ps1` from root for backend + frontend together)
4. Install + run frontend (from `frontend/`):
   ```powershell
   npm install
   npm run dev
   ```
   Open http://localhost:5175 — health: `GET http://localhost:8002/api/v1/health` → `{"status":"ok"}`.

## Design

Same v2 design language as the CRM prototype: white surfaces, 1px `ink-100` borders,
12px card radius, brand `#2edebe`, Comfortaa (logo/titles) / Montserrat (labels) /
Open Sans (body) + Plus Jakarta Sans for module headers. See `frontend/tailwind.config.ts`.
