# CLAUDE.md

## Instructions:
1. Always pull the latest repo from GitHub at the start of the session
2. After every response/output, pull-commit-and-push the repo back to GitHub.
3. You are only the delegator -> Always delegate tasks to OpenCode sub agent (muse spark 1.3 xhigh free model). If an OpenCode run fails or times out, start another OpenCode run; never make the edit yourself unless Siva explicitly asks you to.
4. Always commit and push on the `master` branch. Do not create or push to feature branches.
5. Before delegating to OpenCode or starting the test server, read [docs/agent-guide-actions-opencode.md](docs/agent-guide-actions-opencode.md) in turtle-crm (same pattern applies here until this repo has its own).
6. Never start a test server unless Siva explicitly asks for it in that message.

Guidance for Claude Code and any agent working in this repository. Read this first, then `README.md`.

## What this repository is

Turtle Extraction Lab — the agent testing app. Agents are prototyped and rated here,
then moved to the Extraction Service later. FastAPI backend on PostgreSQL with a React
frontend, separate from the CRM.

This repository is GitHub `Siva-Turtle/turtle-extraction-lab`, checked out locally at
`C:\Users\sivaa\OneDrive\SivaAdithya\Coding\VSCode\turtle\turtle-extraction-lab`.

## The 3 repos — read before working

| Repo | GitHub | Local path |
|---|---|---|
| PRD vault (spec — wins on behaviour) | `Siva-Turtle/turtle-memory-prd` | `C:\SivaAdithya\Turtle Memory` |
| CRM prototype (engineering conventions — `docs/BUILD.md` wins there) | `Siva-Turtle/turtle-crm` | `C:\Users\sivaa\OneDrive\SivaAdithya\Coding\VSCode\turtle\turtle-crm` |
| This lab | `Siva-Turtle/turtle-extraction-lab` | `C:\Users\sivaa\OneDrive\SivaAdithya\Coding\VSCode\turtle\turtle-extraction-lab` |

When behaviour is in doubt, the vault wins. Engineering conventions here mirror
`turtle-crm/docs/BUILD.md` (FastAPI + SQLAlchemy sync + React Vite TS + v2 design tokens)
unless this repo's README says otherwise.

Two folders next to this one, `Turtle CRM\CRM-v1-old` and `TurtlCRM-v2`, are the old system
and an abandoned attempt. They are history, not authority. The old
`turtle-extraction-agent` under `Turtle CRM/` is inherited freelancer code under evaluation —
verify claims against source; do not treat it as the target design.

## Layout

```
turtle-extraction-lab/
  backend/    FastAPI application, Python 3.13, managed with uv (port 8002, db turtle_agent_lab)
  frontend/   React application, Vite and TypeScript (port 5175, proxies /api to :8002)
  scripts/    dev.ps1 starts both
```

## Running it

From `backend/`:

```bash
uv run uvicorn app.main:app --reload --port 8002
```

From `frontend/`, `npm run dev` serves on port 5175 and proxies the API.
`VITE_PROXY_TARGET` points the dev server at a different backend.

## Rules that are not negotiable

- **Never read, print or echo any `.env` file.** It holds the database password and the
  OpenRouter key. Use `app.core.config.settings` and never print secrets. Mask connection strings.
- **Only `@turtlefinance.in` addresses can receive anything.** Same testing-phase lock as the CRM.
- **One agent owns one path.** Do not write outside the paths your prompt gave you.
- **Log rows are denormalized snapshots.** Never add a FK from logs to agents/attributes —
  configs change later and the log must stay frozen.

## Current progress — read and update every turn

**Read `C:\SivaAdithya\Turtle Memory\crm_build_progress.md` (in the vault) at the
start of every turn** only for CRM context; this lab's own progress lives in this repo's
commit log and any `PROGRESS.md` if created. At the end of every turn, update whichever
tracks the work done.
