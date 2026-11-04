# Email drafts (Gmail, drafts only)

`ja/gmail_draft.py` builds an RFC-2822 message with `email.message.EmailMessage`
(To/Cc/Subject/body + PDF attachments) and creates a Gmail **draft** via
`POST /gmail/v1/users/me/drafts` (base64url, no padding). It then verifies the
draft with `GET /gmail/v1/users/me/drafts/<id>?format=full`. It never calls any
send endpoint — drafts are the boundary; the human sends.

## Token acquisition
- Refresh token: `~/.mcp-z/tokens.json` keyv store, key ending in
`:mcp-gmail:token` for the candidate's account (derived from the key; default placeholder `candidate@example.com`), parsed via
  `json.loads(value)["value"]["refreshToken"]`.
- Client id/secret: `~/.omp/agent/mcp.json` → `mcpServers.gmail.env`
  (`GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`). Never copied into the repo.

## Attachments
There is no hardcoded attachment default. Production attachments come from the
career-ops PDF pipeline:

```
node generate-pdf.mjs <report.html> --format=letter --report=NNN
```

run before drafting; pass the resulting PDF with `--attach`.

## CLI
```
python3 -m ja.gmail_draft --to ADDR [--cc ADDR...] --subject S \
  (--body TEXT | --body-file PATH) [--attach FILE ...] [--dry-run] [--verify] \
  [--max-bytes N]
```
`--dry-run` builds the MIME and prints To/Cc/Subject/bytes/attachment names/MIME
structure with zero network calls.

## Verification
After creation the module re-reads the draft and confirms to/cc/subject and
attachment filename are present; returns `verified: true` or raises
`DraftVerificationError` (NOT created-verified).

## Hard rule
No code path may reference a send endpoint. Enforced by
`tests/test_email_draft.py` (grep of `ja/gmail_draft.py` and
`ja/channels/email.py`).
