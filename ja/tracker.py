"""Tracker: SQLite + events + stats + career-ops sync."""
import json
import os
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

from . import config


SCHEMA_PATH = Path(__file__).resolve().parent.parent / "sql" / "schema.sql"


def db_path():
    return config.career_ops_dir() / "data" / "job-autopilot.db"


def _connect():
    p = db_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p))
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = _connect()
    schema = SCHEMA_PATH.read_text() if SCHEMA_PATH.exists() else ""
    conn.executescript(schema)
    conn.commit()
    conn.close()


def _today():
    return date.today().isoformat()


def _now():
    return datetime.now().isoformat(timespec="seconds")


def upsert_job(job):
    conn = _connect()
    try:
        conn.execute(
            """INSERT INTO jobs (job_id, company, role, url, apply_url, ats, channel,
               source, location_text, worldwide_ok, posted_at, discovered_at,
               gate_decision, gate_score, gate_reasons, dedupe_hash)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(job_id) DO UPDATE SET
                 gate_decision=excluded.gate_decision,
                 gate_score=excluded.gate_score,
                 gate_reasons=excluded.gate_reasons""",
            (job["job_id"], job["company"], job["role"], job.get("url"),
             job.get("apply_url"), job.get("ats"), job.get("channel"),
             job.get("source"), job.get("location_text"),
             1 if job.get("worldwide_ok") else (0 if job.get("worldwide_ok") is False else None),
             job.get("posted_at"), _now(),
             job.get("gate_decision"), job.get("gate_score"),
             json.dumps(job.get("gate_reasons", [])), job.get("dedupe_hash")),
        )
        conn.commit()
    finally:
        conn.close()


def record_event(kind, job_id=None, application_id=None, detail=None, dry_run=False):
    ts = _now()
    if not dry_run:
        conn = _connect()
        try:
            conn.execute(
                "INSERT INTO events (ts, job_id, application_id, kind, detail) VALUES (?,?,?,?,?)",
                (ts, job_id, application_id, kind, json.dumps(detail) if detail is not None else None),
            )
            conn.commit()
        finally:
            conn.close()
    # mirror JSONL
    d = date.today().isoformat()
    run_dir = config.runtime_dir() / "runs" / d
    run_dir.mkdir(parents=True, exist_ok=True)
    if not dry_run or True:
        with open(run_dir / "events.jsonl", "a") as f:
            f.write(json.dumps({"ts": ts, "job_id": job_id,
                                "application_id": application_id, "kind": kind,
                                "detail": detail}) + "\n")


VALID_STATUSES = {"discovered", "gated", "rejected", "shortlisted", "drafted",
                  "prefilled", "submitted_auto", "submitted_human", "acknowledged",
                  "screening", "interview", "offer", "rejected_by_employer",
                  "withdrawn", "ghosted"}


def create_application(job_id, channel="form", status="discovered", notes=None, dry_run=False):
    if status not in VALID_STATUSES:
        raise ValueError("invalid status " + status)
    if dry_run:
        return None
    conn = _connect()
    try:
        cur = conn.execute(
            "INSERT INTO applications (job_id, channel, status, notes) VALUES (?,?,?,?)",
            (job_id, channel, status, notes),
        )
        aid = cur.lastrowid
        conn.commit()
        record_event("application_created", job_id=job_id, application_id=aid,
                     detail={"status": status}, dry_run=False)
        return aid
    finally:
        conn.close()


def set_status(app_id, status, dry_run=False):
    if status not in VALID_STATUSES:
        raise ValueError("invalid status " + status)
    if dry_run:
        print("dry-run: would set", app_id, "->", status)
        return
    conn = _connect()
    try:
        conn.execute("UPDATE applications SET status=? WHERE application_id=?",
                     (status, app_id))
        conn.commit()
        record_event("status_change", application_id=app_id, detail={"status": status})
    finally:
        conn.close()


def stats():
    conn = _connect()
    try:
        out = {}
        out["jobs_total"] = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        out["gate_decisions"] = [dict(r) for r in conn.execute(
            "SELECT gate_decision, COUNT(*) c FROM jobs GROUP BY gate_decision").fetchall()]
        out["rejection_hist"] = [dict(r) for r in conn.execute(
            "SELECT gate, COUNT(*) c FROM rejections GROUP BY gate").fetchall()]
        out["applications_by_status"] = [dict(r) for r in conn.execute(
            "SELECT status, COUNT(*) c FROM applications GROUP BY status").fetchall()]
        # funnel per ISO week: count applications created per week
        out["funnel_week"] = [dict(r) for r in conn.execute(
            "SELECT strftime('%Y-W%W', drafted_at) wk, COUNT(*) c FROM applications GROUP BY wk").fetchall()]
        # per-source conversion
        out["per_source"] = [dict(r) for r in conn.execute(
            "SELECT j.source, COUNT(a.application_id) apps FROM jobs j LEFT JOIN applications a ON a.job_id=j.job_id GROUP BY j.source").fetchall()]
        # spam count
        out["spam_count"] = conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE gate_decision='reject_hard'").fetchone()[0]
        # drafts vs submitted
        out["drafts"] = conn.execute(
            "SELECT COUNT(*) FROM applications WHERE status='drafted'").fetchone()[0]
        out["submitted"] = conn.execute(
            "SELECT COUNT(*) FROM applications WHERE status LIKE 'submitted%'").fetchone()[0]
        return out
    finally:
        conn.close()


def record_rejection(job_id, gate, reason, dry_run=False):
    if dry_run:
        return
    conn = _connect()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO rejections (job_id, gate, reason, ts) VALUES (?,?,?,?)",
            (job_id, gate, reason, _now()),
        )
        conn.commit()
    finally:
        conn.close()


def sync():
    """Write career-ops-readable TSV to batch/tracker-additions/<date>.tsv."""
    conn = _connect()
    try:
        rows = conn.execute(
            """SELECT a.application_id, a.job_id, j.company, j.role, j.gate_score,
                      a.status, a.drafted_at FROM applications a JOIN jobs j
                      ON j.job_id=a.job_id ORDER BY a.application_id"""
        ).fetchall()
    finally:
        conn.close()
    out_dir = config.career_ops_dir() / "batch" / "tracker-additions"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / (date.today().isoformat() + ".tsv")
    lines = []
    # header
    lines.append("| # | Date | Company | Role | Score | Status | PDF | Report | Notes |")
    for r in rows:
        d = (r["drafted_at"] or _today())[:10]
        lines.append("| {} | {} | {} | {} | {} | {} |  |  |  |".format(
            r["application_id"], d, r["company"], r["role"],
            r["gate_score"] if r["gate_score"] is not None else 0,
            r["status"]))
    path.write_text("\n".join(lines) + "\n")
    print("synced", len(rows), "rows ->", path)
    return path


def funnel_by_week():
    conn = _connect()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT strftime('%Y-W%W', discovered_at) wk, COUNT(*) c FROM jobs GROUP BY wk"
        ).fetchall()]
    finally:
        conn.close()
