# Handoff — current program status

## Verified working
- Dataset discovery: 886 companies / liveness / pass2 (remoteintech).
- Tracker + gates + stats (SQLite + JSONL event log + career-ops sync).
- Gmail draft channel: real draft created and verified —
  draft id `r3353383145661712692`, to candidate's own address, subject
  `[job-autopilot test draft — safe to delete]`, attachment
  (newest `*.pdf` under `~/career-ops/output/`), `verified: true`, re-read via
  `drafts.get` confirms persistence. No send endpoint contacted.
- Form channel: verified with submit blocked (default no auto-submit).
- Captcha engine: verified on Chromium test sitekey.

## NOT verified
- Production Turnstile with a real widget and proxy.
- Camoufox CLI path.
- Nothing has ever been submitted to a real employer.

## Open decisions
- Whether to enable `allow_automated_submit` (default false).
- Residential/WARP proxy cost for Turnstile.
- Public repo push timing (only after clean pii_audit on fresh clone).
MD