# Agent session guide

## Read-only (do not edit; owned by other workers or external)
- `ja/*.py`, `tests/*`, `sql/*`, `config/*`
- `scripts/pii_audit.sh`
- `~/.mcp-z/tokens.json`, `~/.omp/agent/mcp.json` (secrets; read only)

## Editable (docs + repo metadata)
- `docs/*`, `LICENSE`, `.github/workflows/*`, `README.md`

## Command sequence for a session
1. `cd ~/job-autopilot`
2. `python3 -m ja doctor` — verify paths.
3. `python3 -m ja discover` — fetch jobs.
4. `python3 -m ja gate` — apply gates.
5. `python3 -m ja stats` — inspect funnel.
6. Draft email (drafts only): `python3 -m ja.gmail_draft ... --verify`.
7. `python3 -m ja selftest` — run all tests.
8. `bash scripts/pii_audit.sh` — must exit 0 before any push.

## Artifact paths
- DB: `~/career-ops/data/job-autopilot.db`
- Runtime: `~/.job-autopilot/` (state, jobs.jsonl, shortlist.json, runs/)
- Career-ops: `~/career-ops/` (CV source of truth, PDF pipeline, tracker)
- Drafts: Gmail account (drafts folder), verified via `drafts.get`

## Irreversible actions
Recorded in `events` (SQLite + JSONL) before they happen. Drafts are the
boundary for email; the human sends. No submit without two-key rule.
MD