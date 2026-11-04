CREATE TABLE IF NOT EXISTS jobs (job_id TEXT PRIMARY KEY, company TEXT NOT NULL, role TEXT NOT NULL, url TEXT, apply_url TEXT, ats TEXT, channel TEXT, source TEXT, location_text TEXT, worldwide_ok INTEGER, posted_at TEXT, discovered_at TEXT NOT NULL, gate_decision TEXT, gate_score INTEGER, gate_reasons TEXT, dedupe_hash TEXT UNIQUE);
CREATE TABLE IF NOT EXISTS applications (application_id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL REFERENCES jobs(job_id), channel TEXT NOT NULL, status TEXT NOT NULL, drafted_at TEXT, submitted_at TEXT, artifact_dir TEXT, draft_id TEXT, submitted_by TEXT, notes TEXT);
CREATE TABLE IF NOT EXISTS events (event_id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, job_id TEXT, application_id INTEGER, kind TEXT NOT NULL, detail TEXT);
CREATE TABLE IF NOT EXISTS rejections (job_id TEXT PRIMARY KEY REFERENCES jobs(job_id), gate TEXT NOT NULL, reason TEXT NOT NULL, ts TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS drafts (draft_id TEXT PRIMARY KEY, job_id TEXT NOT NULL, gmail_draft_id TEXT, to_addr TEXT, cc_addr TEXT, subject TEXT, attachments TEXT, verified_at TEXT, created_at TEXT NOT NULL);

CREATE INDEX IF NOT EXISTS idx_jobs_company ON jobs(company);
CREATE INDEX IF NOT EXISTS idx_jobs_gate_decision ON jobs(gate_decision);
CREATE INDEX IF NOT EXISTS idx_applications_status ON applications(status);
CREATE INDEX IF NOT EXISTS idx_events_job_id ON events(job_id);
