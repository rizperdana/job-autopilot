# Daily loop (P6)

`python3 -m ja loop` runs: discover → gate → route (draft | prefill-handoff) → sync → report.

## Step map
1. **discover** — normalize remoteintech + portals.yml into `jobs.jsonl`.
2. **gate** — G1–G7 + scored gates; shortlist sorted by score.
3. **route** — per job: email channel → Gmail draft (never sent); form channel → prefill + one-click handoff (never auto-submit). Caps and idempotency checked per job.
4. **sync** — TSV row(s) into `~/career-ops/batch/tracker-additions/`.
5. **report** — markdown dashboard to `~/.job-autopilot/reports/<date>.md`.

## Caps (from config/ja.yml policy, defaults in ja.example.yml)
- `max_drafts_per_day` (default 10) — N+1th draft is refused, logged `cap reached`.
- `max_submissions_per_day` (default 0) — submissions disabled entirely.
- Two-key submit rule: `allow_automated_submit` (config) **and** `confirm_submit` (per-run `--confirm-submit`). Either false → no submit path is reachable.

## Dry-run guarantees
`--dry-run` performs the whole sequence with **zero network writes and zero side effects**: no DB rows, no Gmail API call, no browser launch, no files beyond `state.json` phase notes and `*_planned_dry` event lines. Draft/handoff functions are never invoked — it prints `WOULD draft …` / `WOULD prefill …` for each planned action.

## Idempotency
Jobs already `drafted`/`prefilled`/`submitted*` in the tracker are skipped (`already <status>`), so re-runs never draft the same job twice. `--limit N` bounds work per run; remaining jobs wait for the next run.

## Crash recovery
Every step updates `~/.job-autopilot/state.json` (`phase`, `current_task`, `last_updated`); every action appends to `~/.job-autopilot/runs/<date>/events.jsonl`. Irreversible actions (draft creation) are recorded as `draft_intent` **before** the Gmail call, so a crash can be reconciled from the log. Resume: re-run `python3 -m ja loop` — idempotency skips what already happened; check `events.jsonl` first.

## Safety switches (stop everything)
- `config.policy.allow_automated_submit: false` (default) + never pass `--confirm-submit`.
- Kill the running process; drafts already created stay in Gmail (candidate decides).
- Caps to 0 in `config/ja.yml`: `max_drafts_per_day: 0` halts drafting.
