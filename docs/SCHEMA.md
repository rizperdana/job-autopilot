# Tracker schema

SQLite (stdlib `sqlite3`) at `~/career-ops/data/job-autopilot.db`. Schema in
`sql/schema.sql` (reproduced here). `ja/tracker.py` owns all access.

## Tables

```sql
CREATE TABLE IF NOT EXISTS jobs (
  job_id TEXT PRIMARY KEY,
  company TEXT NOT NULL,
  role TEXT NOT NULL,
  url TEXT,
  apply_url TEXT,
  ats TEXT,
  channel TEXT,
  source TEXT,
  location_text TEXT,
  worldwide_ok INTEGER,
  posted_at TEXT,
  discovered_at TEXT NOT NULL,
  gate_decision TEXT,
  gate_score INTEGER,
  gate_reasons TEXT,
  dedupe_hash TEXT UNIQUE
);
CREATE TABLE IF NOT EXISTS applications (
  application_id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id TEXT NOT NULL REFERENCES jobs(job_id),
  channel TEXT NOT NULL,
  status TEXT NOT NULL,
  drafted_at TEXT,
  submitted_at TEXT,
  artifact_dir TEXT,
  draft_id TEXT,
  submitted_by TEXT,
  notes TEXT
);
CREATE TABLE IF NOT EXISTS events (
  event_id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  job_id TEXT,
  application_id INTEGER,
  kind TEXT NOT NULL,
  detail TEXT
);
CREATE TABLE IF NOT EXISTS rejections (
  job_id TEXT PRIMARY KEY REFERENCES jobs(job_id),
  gate TEXT NOT NULL,
  reason TEXT NOT NULL,
  ts TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS drafts (
  draft_id TEXT PRIMARY KEY,
  job_id TEXT NOT NULL,
  gmail_draft_id TEXT,
  to_addr TEXT,
  cc_addr TEXT,
  subject TEXT,
  attachments TEXT,
  verified_at TEXT,
  created_at TEXT NOT NULL
);
```

Indexes: `idx_jobs_company`, `idx_jobs_gate_decision`, `idx_applications_status`,
`idx_events_job_id`.

## Status enum (applications.status)

`discovered → gated → rejected | shortlisted → drafted | prefilled →
submitted(AUTO|HUMAN) → acknowledged → screening → interview →
offer | rejected_by_employer | withdrawn | ghosted`

## Event log

`record_event(kind, job_id=None, application_id=None, detail=None, dry_run=False)`
writes to SQLite `events` and appends a JSONL line to
`~/.job-autopilot/runs/<date>/events.jsonl`:

```json
{"ts": "...", "kind": "...", "job_id": "...", "application_id": 1,
 "detail": {...}}
```

## Stats queries (ja/tracker.py)

- `stats()` — aggregate counts by status/channel.
- `funnel_by_week()` — `SELECT strftime('%Y-W%W', discovered_at) wk, COUNT(*) c
  FROM jobs GROUP BY wk`.
- `sync()` — writes career-ops TSV to
  `~/career-ops/batch/tracker-additions/<date>.tsv`.

## Functions

`db_path, init_db, upsert_job, record_event, create_application, set_status,
record_rejection, sync, stats, funnel_by_week`.
PY