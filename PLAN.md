# job-autopilot — plan & architecture

## 1. Goal & scope

Find and prepare applications for verified-remote jobs that accept worldwide applicants, matched to the candidate's real skills, from the remoteintech company list plus the candidate's existing career-ops portal list. Channels: ATS web forms (browser automation, human-in-the-loop) and email applications (Gmail DRAFT created, never sent — the candidate sends). Everything is tracked with statistics. Deliverables include a public PII-free repo under GitHub `rizperdana` and a reusable agent skill.

Non-goals: no fabricated experience, no fake accounts, no bulk spam, no scraping behind logins, no auto-send of email, no applying to jobs the gates reject, no storing CV/contact/tokens in the public repo.

## 2. Candidate profile

Single source of truth: `~/career-ops/config/profile.yml` + `~/career-ops/cv.md` (never duplicated into the repo — the repo references this file by path).

Senior Software Engineer / Fullstack / ML Engineer; Yogyakarta, Indonesia; WIB UTC+7; 6+ years Python, Ruby, JavaScript, PHP, SQL; REST APIs, microservices, MySQL/PostgreSQL/Elasticsearch, Celery, Docker, CI/CD, AWS; LLM/RAG integration; side projects Semgrepll, Wikwok, RealityCheck. Target $50K–90K (min $40K). Eligibility rule: fully-remote worldwide ONLY, or onsite/hybrid in a developed country with explicit relocation + visa sponsorship. Requires sponsorship abroad. English professional, Indonesian native. Output language: en.

## 3. Data sources

(a) `remoteintech/remote-jobs` repo (`src/companies/{slug}.md`, YAML frontmatter keys: addedAt, careers_url, company_size, redirectFrom, region, remote_policy, slug, technologies, updatedAt, website; 886 companies; `region: worldwide` = 405; `region=worldwide AND remote_policy in {fully-remote, remote-first}` = 322). (b) ATS public APIs for real liveness: Greenhouse `boards-api.greenhouse.io/v1/boards/<slug>/jobs`, Lever `api.lever.co/v0/postings/<slug>?mode=json`, Ashby `api.ashbyhq.com/posting-api/job-board/<slug>`, Workable `apply.workable.com/api/v1/widget/accounts/<slug>?details=true`, Recruitee/Personio/SmartRecruiters/Workday/Teamtailor equivalents. (c) The candidate's existing `~/career-ops/portals.yml` (135 tracked companies; 22 overlap with the remoteintech list) and `scan.mjs`. (d) The company's own careers page for bespoke sites (HTML + JSON-LD `JobPosting` + embedded ATS detection).

Key measurement lesson from recon: the remoteintech list is a *directory*, not a liveness feed — `updatedAt` reaches back to 2018 and only ~40 of 616 careers URLs sit on a countable ATS, so hiring status MUST be verified against the live ATS API / live posting page per job, never inferred from the list.

## 4. Architecture

Python 3 stdlib only; zero new dependencies unless unavoidable.

```
~/job-autopilot/            # public repo (code + docs + schema ONLY)
  ja/{sources,gates,scam,channels/form,channels/email,tracker,captcha,report,state}.py
  ja/__main__.py            # CLI: discover|gate|draft|apply|sync|stats|report|doctor|selftest
  sql/schema.sql            # tracker schema
  docs/{PLAN,RUNBOOK,SCHEMA,POLICY,AGENTS,HANDOFF}.md
  config/{profile.example.yml,ja.example.yml,rules/*.yml}
  scripts/{pii_audit.sh,gh_switch_rizperdana.sh}
  tests/                    # stdlib unittest self-checks
~/.job-autopilot/           # PRIVATE runtime (never in repo)
  state.json, jobs.jsonl, shortlist.json, runs/<date>/, vendor/turnstile_solver/
~/career-ops/               # existing system; CV source of truth + PDF pipeline + tracker
  data/applications.md, data/applications.db, output/*.pdf, reports/
```

Tracker of record for the user's existing dashboards stays career-ops compatible; the new SQLite is the automation's audit store and syncs INTO career-ops.

- `sources.py` → normalize every candidate job into one record: `{job_id, company, role, url, apply_url, ats, channel, location_text, worldwide_ok, posted_at, source, raw}`; dedupe on `sha256(company|role_normalized|apply_url)`.
- `gates.py` → section 5.
- `channels/email.py` → Gmail draft via REST `POST https://gmail.googleapis.com/gmail/v1/users/me/drafts` with base64url `message/rfc822` (python `email` builds multipart with To/Cc/Subject/body + attachment PDFs). Token comes from the persisted refresh token in `~/.mcp-z/tokens.json` (`key suffix :mcp-gmail:token`), scope already includes `mail.google.com`. The configured MCP exposes no draft tool — `message-send`/`message-respond` MUST NOT be used; drafts are created and verified via the REST API and `drafts.list`/`drafts.get`. Verify each draft by reading it back (to/cc/subject/attachment filename).
- `channels/form.py` → browser layer (section 6).
- `tracker.py` → SQLite + JSONL event log + stats + career-ops sync.
- `captcha.py` → wrapper around the local toolkit.
- `state.py`/`report.py` → resumability + weekly markdown dashboard.

## 5. Validation gates (multi-layer)

Hard gates — any failure = `reject_hard`, no override except the candidate's explicit `--force` with a recorded reason:

G1 authentic employer (company website resolves, ATS/apply domain consistent with the company, not a data-entry/MLM/insurance-recruitment pattern).
G2 live posting (the job exists in the live ATS API or its posting page returns 200 with a real JD body; `posted_at` ≤ 45 days old or unknown-but-live).
G3 geography (worldwide / APAC / Indonesia-eligible, OR onsite/hybrid in a developed country with explicit relocation + visa sponsorship; hard-reject postings that require US/EU work authorization with no sponsorship, and hard-reject onsite-only Indonesia seats).
G4 language (job requires a language the candidate does not have = hard reject; e.g. JP/KR/ZH/German-native mandates).
G5 duplicate/already-applied (same company+role, or company already applied to under a different channel — reuse career-ops' cross-channel rule).
G6 blacklist (`~/career-ops/data/blacklist.md` if present; candidate's own do-not-apply list wins).
G7 spam/scam/free-labor screen (section 5b).

5b extension of the existing apply-mode preflights — reuse these by name and add the new gates beside them:

Existing preflight gates reused (verified in `~/career-ops/modes/apply.md`):
- Step 5 — Preflight gate (liveness + company/role match) — `modes/apply.md:30`
- Blacklist check (#1742) — `modes/apply.md:34`
- Cross-channel check (#1596) — `modes/apply.md:36`
- Repeat-application ATS profile check (#1920) — `modes/apply.md:42`
- Step 5b — Pre-scan for knock-out questions — `modes/apply.md:60`
- Step 5d — Immigration-status screening check (#2033) — `modes/apply.md:77`
- Step 5c — Jurisdiction-prohibited content check (#2018) — `modes/apply.md:97`
- Liveness sweep (pipeline mode, bulk) — `modes/apply.md:115` / `modes/pipeline.md`

`~/career-ops/modes/_shared.md` contains no preflight gates by name — it supplies global rules (NEVER/ALWAYS, posting legitimacy Block G, scoring system, company-type/compensation reliability). No gate names invented.

Scored gates (weighted, total 100): role fit 0–40, stack fit 0–25, seniority fit 0–15, compensation plausibility 0–10, evidence quality (JD completeness + apply path clarity) 0–10. Decisions: ≥60 `apply`, 45–59 `review` (human decides), <45 `skip`. Every decision persists the score breakdown + the reason strings so the rejection histogram is analyzable. All facts about the candidate come from `cv.md`/`profile.yml` only — never invent metrics; an unverifiable claim is a gate failure on the artifact, not a silent edit.

## 6. Submission channels & policy

- Form channel: persistent real browser profile (CloakBrowser/Camoufox/PinchTab or Playwright), fills from a fact-gated answer bank, attaches the career-ops-generated PDF, screenshots the result, and records every field. Default = prefill + one-click handoff, no auto-submit (`config.policy.allow_automated_submit: false`), because the candidate's own career-ops skill records that Ashby/Greenhouse flag headless submissions as possible spam — auto-submission is an explicit, per-run, recorded opt-in by the candidate, never a default.
- Email channel: create a Gmail draft (to, cc if the posting asks, subject, body, attachments) with the application text derived from the tailored cover letter; the candidate sends it. Draft creation is the boundary.
- Captcha policy: this is a legitimate bot-check on the candidate's own application for their own account — attempt it; never create accounts, never solve a login wall for an account that isn't the candidate's, never bulk-solve, max 3 attempts per form then hand the page to the candidate. Toolkit: `~/Downloads/turnstile_solver.zip` (native Playwright/Camoufox JS-hook solver; modes: CLI, Python `TurnstileEngine`, FastAPI on 127.0.0.1:8123; needs `pip install -r requirements.txt` + `playwright install chromium`; proxy env `TURNSTILE_PROXY`/`ENI_TS_PROXY`; its README warns datacenter IPs get low trust → residential/WARP proxy may be required at cost).
- Cost/ToS risk is stated plainly, and the switch is the candidate's.

## 7. Tracking, statistics, and career-ops compatibility

SQLite (via stdlib `sqlite3`) at `~/career-ops/data/job-autopilot.db`:

```sql
CREATE TABLE jobs (job_id TEXT PRIMARY KEY, company TEXT NOT NULL, role TEXT NOT NULL, url TEXT, apply_url TEXT, ats TEXT, channel TEXT, source TEXT, location_text TEXT, worldwide_ok INTEGER, posted_at TEXT, discovered_at TEXT NOT NULL, gate_decision TEXT, gate_score INTEGER, gate_reasons TEXT, dedupe_hash TEXT UNIQUE);
CREATE TABLE applications (application_id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL REFERENCES jobs(job_id), channel TEXT NOT NULL, status TEXT NOT NULL, drafted_at TEXT, submitted_at TEXT, artifact_dir TEXT, draft_id TEXT, submitted_by TEXT, notes TEXT);
CREATE TABLE events (event_id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, job_id TEXT, application_id INTEGER, kind TEXT NOT NULL, detail TEXT);
CREATE TABLE rejections (job_id TEXT PRIMARY KEY REFERENCES jobs(job_id), gate TEXT NOT NULL, reason TEXT NOT NULL, ts TEXT NOT NULL);
CREATE TABLE drafts (draft_id TEXT PRIMARY KEY, job_id TEXT NOT NULL, gmail_draft_id TEXT, to_addr TEXT, cc_addr TEXT, subject TEXT, attachments TEXT, verified_at TEXT, created_at TEXT NOT NULL);
```

Status enum: `discovered → gated → rejected | shortlisted → drafted | prefilled → submitted(AUTO|HUMAN) → acknowledged → screening → interview → offer | rejected_by_employer | withdrawn | ghosted`.

Append-only `events` JSONL mirror at `~/.job-autopilot/runs/<date>/events.jsonl` for auditability.

Stats commands: funnel by ISO week; per-source conversion (remoteintech vs portals.yml vs ad-hoc); gate rejection histogram (which gate kills the most candidates — drives tuning); spam-flagged count; drafts created vs sent; median time from discovery to submission; response rate by channel.

career-ops sync: append one row per application in career-ops' own format into `~/career-ops/batch/tracker-additions/` as `.tsv` (the `.md` variant is silently ignored by their merge script) and keep `~/career-ops/data/applications.md` authoritative for the user's existing dashboards. Real format to embed verbatim (quoted from `~/career-ops/data/applications.md` lines 1–5):

```
# Applications Tracker
<blank>
| # | Date | Company | Role | Score | Status | PDF | Report | Notes |
|---|------|---------|------|-------|--------|-----|--------|-------|
```

## 8. Public repo policy (PII/secrets)

Never committed: CV/contact info, `cv.md`, `config/profile.yml` with real values (only `*.example.yml` with placeholders), `~/.mcp-z/tokens.json`, client ids/secrets, application data, generated PDFs, email addresses, phone numbers, LinkedIn/GitHub tokens, the dataset dumps that embed personal notes. `scripts/pii_audit.sh` greps the staged tree + history HEAD for: email addresses, Indonesian phone-number prefixes, the candidate's name, Google OAuth token fragments, GitHub PAT prefixes, OpenAI-style secret keys, generic API-key literals, and absolute home paths with usernames; exit non-zero on any hit. Wire it as a pre-commit hook and a CI job; the repo is only pushed public after a clean audit on a fresh clone.

## 9. Phases (each = one session; acceptance + verification)

P0 Recon (done): dataset 886 companies / liveness 886 probed / profile snapshot. Verify: `/tmp/remoteintech/REPORT.md` counts + `python3 -m ja doctor` later.
P1 Repo skeleton, schema.sql, tracker, stats, CLI, doctor, self-checks. Verify: `python3 -m ja selftest` and `python3 -m ja stats` on an empty DB.
P2 Gates + scam library + backtest over the real 886-record dataset. Verify: produces `~/.job-autopilot/shortlist.json` with the rejection histogram; spot-check 3 decisions against the live posting.
P3 Email channel (Gmail draft helper + verification). Verify: create one real draft to the candidate's own address, read it back, confirm to/cc/subject/attachment, and confirm nothing was sent.
P4 Form channel on ONE real posting in handoff mode. Verify: filled-field JSON + screenshot + recorded handoff instructions; nothing submitted.
P5 Captcha layer: unzip toolkit to `~/.job-autopilot/vendor/`, install, self-test token fetch on a Turnstile demo page. Verify: token returned; document proxy requirement + cost.
P6 Daily loop with caps, routing, tracker writes, weekly report. Verify: one full dry run end-to-end with `--dry-run` (no submissions), then one live draft.
P7 Publish public under `rizperdana` (switch gh account first: `gh auth switch --user rizperdana`), CI running tests + `pii_audit.sh`, README with a sanitized sample config. Verify: `gh repo view`, CI green, audit clean on a fresh clone.
P8 Skill `job-autopilot-apply` (managed skill) documenting the loop, gates, caps, captcha policy, and the exact commands, plus the career-ops integration points. Verify: skill loads and its commands run.

## 10. Context-loss prevention

`state.json` + `PROGRESS.md` + `HANDOFF.md` updated at the END of every phase; artifacts on disk, never only in context; every worker brief self-contained; all commands idempotent; irreversible actions (draft creation, any submission) recorded BEFORE they happen with the inputs, so a crash can be reconciled.

## 11. Risks & mitigations

ATS anti-bot/spam flagging → default handoff, low volume, human-click path. Account/domain bans → never automate logins or bulk-submit. Legal/ToS → public postings only, no logged-in scraping, policy switch is the candidate's. Captcha IP trust → residential proxy cost, capped attempts. False liveness → always verify against the live ATS API at apply time. PII leak in the public repo → pii_audit gate + example-only configs. Ghosting/ghost-jobs → status `ghosted` after 21 days, report funnel honestly. Long-context loss → section 10.

## 12. Needs candidate decision (explicit)

(1) `allow_automated_submit`: false (default) or true, and for which ATS? (2) Daily caps: drafts/day and submissions/day. (3) Unpaid take-home/trial policy (allow ≤2h? reject all?). (4) Captcha proxy budget (residential proxy is likely required for Turnstile; how much per month?). (5) Which countries count as "developed country, relocation + sponsorship OK". (6) Which CV/PDF variants to attach by default. (7) OK to state Indonesian residency/phone on forms that demand local residency?
