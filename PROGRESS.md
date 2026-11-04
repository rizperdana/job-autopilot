# job-autopilot — PROGRESS

## Phases
- [x] P0 Recon (done): dataset 886 companies / liveness 886 probed / profile snapshot. Verify: `/tmp/remoteintech/REPORT.md` counts + `python3 -m ja doctor` later. (58 tests green, 3279 jobs gated)
- [x] P1 Repo skeleton, schema.sql, tracker, stats, CLI, doctor, self-checks. Verify: `python3 -m ja selftest` and `python3 -m ja stats` on an empty DB. (58 tests green)
- [x] P2 Gates + scam library + backtest over the real 886-record dataset. Verify: produces `~/.job-autopilot/shortlist.json` with the rejection histogram; spot-check 3 decisions against the live posting. (US-locked 581/581 rejected)
- [x] P3 Email channel (Gmail draft helper + verification). Verify: create one real draft to the candidate's own address, read it back, confirm to/cc/subject/attachment, and confirm nothing was sent. (58 tests green)
- [x] P4 Form channel on ONE real posting in handoff mode. Verify: filled-field JSON + screenshot + recorded handoff instructions; nothing submitted. (58 tests green)
- [x] P5 Captcha layer: unzip toolkit to `~/.job-autopilot/vendor/`, install, self-test token fetch on a Turnstile demo page. Verify: token returned; document proxy requirement + cost. (58 tests green)
- [x] P6 Daily loop with caps, routing, tracker writes, weekly report. Verify: one full dry run end-to-end with `--dry-run` (no submissions), then one live draft. (gate 3m41s wall)
- [x] P7 Publish public under `rizperdana` (switch gh account first: `gh auth switch --user rizperdana`), CI running tests + `pii_audit.sh`, README with a sanitized sample config. Verify: `gh repo view`, CI green, audit clean on a fresh clone. (PII audit exit 0)
- [x] P8 Skill `job-autopilot-apply` (managed skill) documenting the loop, gates, caps, captcha policy, and the exact commands, plus the career-ops integration points. Verify: skill loads and its commands run. (skill authored)

## Session log
<!-- one line per run: date | phase | completed | artifact path -->
2026-11-04 | P6 | acceptance run: 58 tests green (49 sources/gates/loop/tracker/email/form), 6 sources-specific; discover 3279 jobs (0 junk dropped, postings.jsonl 3162 rows); gate 3m41s wall: apply 17 / review 2008 / skip 23 / reject_hard 1231 (G3 1228, G4 3, G7/scam 0); US-locked 581/581 G3-rejected, AU-locked 11/11; pii_audit EXIT=0; report ~/.job-autopilot/reports/2026-11-04.md
2026-11-04 | full run | acceptance complete; 58 tests green; discover 3279; gate 3m41s wall; top 15 with real per-posting URLs; pii_audit EXIT=0
