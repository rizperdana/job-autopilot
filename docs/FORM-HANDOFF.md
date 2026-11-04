# Form handoff — job-autopilot

## Artifacts

Each run writes to `~/.job-autopilot/runs/<date>/<job_id>/`:

- `filled.json` — ATS, detected fields, filled values, needs_human list,
  turnstile result, actions taken, artifact paths.
- `steps.jsonl` — append-only step log.
- `form.png` — full-page screenshot of the filled (unsubmitted) form.
- `handoff.md` — human-readable summary + next actions.

## What the human does

1. Open `form.png` and `filled.json`.
2. Correct any `NEEDS_HUMAN` fields (unknowns are never guessed).
3. Solve the captcha manually if `needs_human_captcha: true` (3 attempts exhausted).
4. Click submit manually. Auto-submit is OFF unless the candidate explicitly
   opts in per run (`allow_automated_submit` + `confirm_submit=True`).

## Two-key submit rule

`prepare_application(..., allow_submit=False, confirm_submit=False)`. A click
happens only when BOTH are True. Default guarded path never clicks.

## Caps

- Turnstile: max 3 attempts per form, then hand to human.
- Never create accounts, never solve a login wall for an account that isn't
  the candidate's, never bulk-solve.