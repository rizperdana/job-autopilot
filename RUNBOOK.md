# job-autopilot — RUNBOOK

## 1. Daily loop

Run in order:
1. `python3 -m ja discover` — normalize jobs from remoteintech + portals.yml into the tracker; artifacts in `~/.job-autopilot/runs/<date>/`.
2. `python3 -m ja gate` — apply G1–G7 + scored gates; writes `~/.job-autopilot/shortlist.json` + rejection histogram.
3. `python3 -m ja draft` — draft emails/forms for shortlisted jobs (Gmail drafts created, not sent).
4. `python3 -m ja apply` — form channel: prefill + one-click handoff (no auto-submit by default).
5. `python3 -m ja sync` — sync applications into career-ops (`batch/tracker-additions/*.tsv`).
6. `python3 -m ja report` — weekly markdown dashboard.

Expected output: funnel counts per step; artifacts land under `~/.job-autopilot/runs/<date>/` and `~/.job-autopilot/shortlist.json`.

## 2. Reviewing drafts

- List pending drafts: `python3 -m ja drafts list` (or `drafts.list` via Gmail REST).
- Verify each draft by reading it back: to/cc/subject/attachment filename must match the job.
- Send: candidate sends from Gmail UI. The automation never sends.

## 3. Form handoff

- Prefilled page + artifacts: filled-field JSON + screenshot + handoff instructions under `runs/<date>/`.
- Solve captcha yourself if the solver fails or attempts exceed 3; then click Submit.

## 4. Recording outcomes

- Move status via `python3 -m ja status <id> <status>` (acknowledged/screening/interview/offer/rejected/ghosted).
- Ghost rule: mark `ghosted` after 21 days with no response.

## 5. Adding a company or job by hand

- Ad-hoc URL path: `python3 -m ja discover --url <url>` (or insert into the tracker directly); re-run `gate`.

## 6. Crash recovery

- Resume from `state.json` (`phase`, `current_task`, `blockers`).
- Re-runs are idempotent; check `runs/<date>/events.jsonl` for what already happened.

## 7. Troubleshooting table

| Problem | Fix |
|---|---|
| Token expired | Re-authenticate Gmail via `~/.mcp-z/tokens.json` flow |
| ATS 403 | Switch proxy / retry; hand off to candidate |
| Captcha loop | Solve manually; check proxy env `TURNSTILE_PROXY` |
| Tracker not syncing | Re-run `sync`; check `.tsv` in `batch/tracker-additions/` |
| PDF missing | Re-run career-ops PDF pipeline |
| Duplicate application warning | Check cross-channel rule in apply-mode preflight |

## 8. Safety switches

- `config.policy.allow_automated_submit`: false (default). Set true only with recorded per-run opt-in.
- Daily caps: set in config; enforced by the daily loop.
- Stop everything: set `allow_automated_submit: false` and kill the running process; drafts already created remain (candidate decides).
