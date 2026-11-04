# Submission policy

## Default posture
- No automated submission by default: `config.policy.allow_automated_submit` is `false`. Default flow is prefill + one-click handoff by the candidate.
- Auto-submission is an explicit, per-run, recorded opt-in (`--force` with a recorded reason), never a default. Reason: Ashby/Greenhouse flag headless submissions as possible spam.

## Two-key submit rule
Both must be true before any submit: `allow_submit` (config) and `confirm_submit` (per-run confirmation). Either false → no submit.

## Form channel
- Persistent real browser profile (CloakBrowser/Camoufox/PinchTab/Playwright).
- Fills from a fact-gated answer bank; attaches the career-ops PDF.
- Screenshots the result; records every field.
- Default = prefill + one-click handoff.

## Email channel
- Drafts only, never send. The human sends.
- Code-level ban: no reference to `users.messages.send`, `users.drafts.send`, or any `/send` endpoint anywhere in `ja/gmail_draft.py` or `ja/channels/email.py` (enforced by `tests/test_email_draft.py`).

## Captcha policy
- Max 3 attempts per form, then hand the page to the candidate.
- Never create accounts; never solve a login wall that is not the requester's own; never bulk-solve.
- Legitimate bot-check on the candidate's own application for their own account — attempt it.

## PII / secret policy (PLAN.md section 8)
- Never committed: CV/contact info, `cv.md`, `config/profile.yml` with real values (only `*.example.yml` with placeholders), `~/.mcp-z/tokens.json`, client ids/secrets, application data, generated PDFs, email addresses, phone numbers, LinkedIn/GitHub tokens, dataset dumps with personal notes.
- Only `*.example.yml` with placeholders; placeholders like `candidate@example.com`, `+00 000 000 000`.
- `scripts/pii_audit.sh` greps the staged tree + history HEAD for emails, phone patterns, the candidate's name, the Google OAuth client-secret prefix, Google access-token prefix, refresh-token prefix, GitHub PAT prefixes, sk prefixed keys, api_key, absolute home paths with usernames (placeholder form); exits non-zero on any hit. Wire as pre-commit hook and CI job.
- Repo pushed public only after a clean audit on a fresh clone.
MD