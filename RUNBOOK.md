# job-autopilot — RUNBOOK

## 1. Daily loop

Run in order:
1. `python3 -m ja discover` — normalize jobs from remoteintech + portals.yml into the tracker; artifacts in `~/.job-autopilot/runs/<date>/`.
2. `python3 -m ja gate` — apply G1–G7 + scored gates; writes `~/.job-autopilot/shortlist.json` + rejection histogram.
3. `python3 -m ja draft` — draft emails/forms for shortlisted jobs (Gmail drafts created, not sent).
4. `python3 -m ja apply` — form channel: prefill + one-click handoff (no auto-submit by default).
5. `python3 -m ja batch-apply --limit N [--max-submissions N] [--confirm-submit]` — form channel: measure
   each candidate, submit only the ones that pass the gate (`pct >= 90` AND nothing required blank AND no
   captcha/auth wall). The gates are re-run at selection time (a stored decision in `shortlist.json` is a
  cache with no invalidation key) and only engineering roles (rank <= 2) are drawn. Before any browser
  starts, one GET on each candidate's `apply_url` refuses a 404/410 (dead posting) and, separately, a
  403/429 (a wall in front of a live page); both are counted in the tally and every other outcome —
  5xx, timeout, DNS — counts as alive so a blip never drops a job. `--dry-run` measures with no submit.
  Never submits without `--confirm-submit`, and never past the submission cap (config default 0 = refuse
  everything; the per-run `--max-submissions` override is reported). Email-channel jobs are drafts the
  candidate sends, never counted here.
  A captcha/auth wall stops the batch at that page; for a measurement sweep
  (`--dry-run --keep-going-wall`) it walks on instead — the flag only changes whether the loop breaks,
  never what may be submitted.
6. `python3 -m ja sync` — sync applications into career-ops (`batch/tracker-additions/*.tsv`).
7. `python3 -m ja report` — weekly markdown dashboard.
Expected output: funnel counts per step; artifacts land under `~/.job-autopilot/runs/<date>/` and
`~/.job-autopilot/shortlist.json`; `batch-apply` appends one JSON line per attempt to
`~/.job-autopilot/reports/batch-apply-<date>.jsonl`.

## 2. Reviewing drafts

- List pending drafts: `python3 -m ja drafts list` (or `drafts.list` via Gmail REST).
- Verify each draft by reading it back: to/cc/subject/attachment filename must match the job.
- Send: candidate sends from Gmail UI. The automation never sends.

## 3. Form handoff

- Prefilled page + artifacts: filled-field JSON + screenshot + handoff instructions under `runs/<date>/`.
- Field labels come from one ladder per control: `label[for]` -> wrapping `<label>` ->
  `aria-labelledby` -> `aria-label` -> preceding sibling text -> `fieldset > legend` -> the closest
  block heading -> the placeholder. The source is kept: a placeholder-derived label appears in
  `confidence.json` with `label_source: "placeholder"` and reads "(placeholder only)", and
  `confidence.placeholder_only` counts them. A required control with no name, no id and no readable
  question text is counted in `unresolved_required`, never scored and never blocking.
- Class (a) facts beyond contact details are read out of the candidate's own files, verbatim:
  total years from the CV prose ("… with 8 years") and degree / school / discipline from the one
  `## Education` line (`- Bachelor Degree, Accounting -- Yogyakarta State University (2016 - 2020)`).
  The same line in another shape yields nothing. Refused on purpose: a per-skill years question
  ("… with Kubernetes"), a bare dropdown option ("2-4 years", "10+ years"), "Nanodegree", and any
  prose question mentioning school or degree -- those stay `NEEDS_HUMAN`.
- A `<select>` is filled by option text, never by index: the mapped value is compared word by word
  (a 4+-char prefix counts, so "Bachelor Degree" matches "Bachelor's Degree") and an option naming
  more than the value is refused. No option matches -> the field is reported, not guessed.
- The CV is uploaded to the file input the page wants for a resume, not merely the first one: each
  box is scored by its ladder label, a box labelled as a cover letter never wins, and a page whose
  only file boxes are cover letters gets no upload. A second, still-empty required file box is not
  credited by the resume upload, and a file input we did fill is no longer listed as needing a human.
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
- `policy.max_submissions_per_day`: 0 (default) means no submission is authorized. `batch-apply
  --max-submissions N` overrides it for that one run and prints the override; the runner never rewrites
  the config file.
- Stop everything: set `allow_automated_submit: false` and kill the running process; drafts already created remain (candidate decides).
