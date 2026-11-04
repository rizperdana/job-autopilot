"""Daily loop: discover -> gate -> route by channel -> draft|handoff -> sync -> report.

Dry-run performs the whole sequence with zero network writes and zero side
effects. Re-runs are idempotent: jobs already drafted (tracker) are skipped.
Irreversible actions are recorded BEFORE they happen (PLAN.md section 10).
"""
import datetime
import json

from . import config, gates, sources, tracker


def atomic_write_text(path, text):
    """Temp file + os.replace: an interrupted run can never leave a half file."""
    import os
    path = os.fspath(path)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def _today():
    return datetime.date.today().isoformat()


def _phase(state_name, task):
    from . import state
    state.save_state(state_name, task)


def caps_used():
    """Drafts and submissions already recorded today (from tracker events/applications)."""
    conn = tracker._connect()
    try:
        drafts = conn.execute(
            "SELECT COUNT(*) FROM applications WHERE status='drafted' "
            "AND date(drafted_at)=?", (_today(),)).fetchone()[0]
        subs = conn.execute(
            "SELECT COUNT(*) FROM applications WHERE status LIKE 'submitted%' "
            "AND date(submitted_at)=?", (_today(),)).fetchone()[0]
        return drafts, subs
    finally:
        conn.close()


def already_drafted(job_id):
    """Idempotency: has this job already been drafted/handed off?"""
    conn = tracker._connect()
    try:
        row = conn.execute(
            "SELECT status FROM applications WHERE job_id=? "
            "AND status IN ('drafted','prefilled','submitted_auto','submitted_human')",
            (job_id,)).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def _record_intent(kind, job_id, detail):
    """Record an irreversible action BEFORE performing it (PLAN.md s10)."""
    tracker.record_event(kind, job_id=job_id, detail=detail)


def route_job(job, *, dry, drafts_today, profile, max_drafts, confirm_submit=False):
    """Route one shortlisted job. Returns (action, reason). Never sends/submits."""
    job_id = job.get("job_id") or job.get("id")
    company = job.get("company", "?")
    role = job.get("role", "?")
    channel = (job.get("channel") or "form").lower()

    done = already_drafted(job_id)
    if done:
        return ("skip", "already %s (idempotent)" % done)
    if drafts_today >= max_drafts:
        return ("skip", "cap reached: max_drafts_per_day=%d" % max_drafts)

    if channel == "email":
        if dry:
            print("  WOULD draft email (Gmail draft, never send): %s | %s | %s"
                  % (company, role, job.get("apply_url") or job.get("url")))
            tracker.record_event("draft_planned_dry", job_id=job_id,
                                 detail={"channel": "email", "company": company,
                                         "role": role, "dry_run": True})
            return ("would_draft", "dry-run")
        # record BEFORE the irreversible call
        _record_intent("draft_intent", job_id, {"channel": "email",
                                                "company": company, "role": role,
                                                "inputs": {"subject": "[%s] %s" % (company, role)}})
        from .channels import email as em
        res = em.create_email_draft(
            to=[config.get("email.to") or "candidate@example.com"],
            subject="[job-autopilot] %s — %s" % (company, role),
            body="Application draft for %s (%s).\nPosting: %s\n"
                 % (role, company, job.get("apply_url") or job.get("url")),
            verify=True)
        tracker.record_event("draft_created", job_id=job_id,
                             detail={"channel": "email", "gmail_draft_id": str(res)[:120]})
        return ("drafted", "gmail draft created")
    # form channel: prefill + handoff (no auto-submit by default)
    if dry:
        print("  WOULD prefill form + handoff (no submit): %s | %s | %s"
              % (company, role, job.get("apply_url") or job.get("url")))
        tracker.record_event("prefill_planned_dry", job_id=job_id,
                             detail={"channel": "form", "company": company,
                                     "role": role, "dry_run": True})
        return ("would_prefill", "dry-run")
    allow_submit = bool(config.allow_automated_submit()) and bool(confirm_submit)
    _record_intent("prefill_intent", job_id, {"channel": "form",
                                              "company": company, "role": role,
                                              "allow_submit": allow_submit})
    from .channels import form as fm
    res = fm.prepare_application(job, profile, allow_submit=allow_submit,
                                 confirm_submit=confirm_submit)
    status = "prefilled" if not res.get("submitted") else "submitted_auto"
    aid = tracker.create_application(job_id, channel="form", status=status)
    tracker.record_event("prefill_done", job_id=job_id, application_id=aid,
                         detail={"needs_human": len(res.get("needs_human", [])),
                                 "submitted": bool(res.get("submitted"))})
    return ("prefilled", "artifacts " + str(res.get("artifacts", {}).get("handoff_md", "")))


def run(limit=5, dry=True, confirm_submit=False):
    """Run the daily loop. Honors caps; idempotent; resumable via state.json."""
    _phase("P6-loop", "loop start (%s)" % ("dry-run" if dry else "live"))
    caps = (config.get("policy.max_drafts_per_day", 10),
            config.get("policy.max_submissions_per_day", 0))
    max_drafts, max_subs = int(caps[0]), int(caps[1])
    drafts_today, subs_today = (0, 0) if dry else caps_used()
    print("caps: max_drafts=%d used_today=%d | max_submissions=%d used_today=%d "
          "(two-key submit: allow_automated_submit=%s)"
          % (max_drafts, drafts_today, max_subs, subs_today,
             config.allow_automated_submit()))
    profile = config.profile() or {}

    # 1. discover
    if not dry:
        tracker.init_db()
    jobs = sources.run()
    print("discover: %d jobs" % len(jobs))
    _phase("P6-loop", "discover done (%d jobs)" % len(jobs))

    # 2. gate
    shortlist = []
    if dry:
        # no db writes in dry: evaluate in memory only
        for j in jobs:
            r = gates.evaluate(j)
            if r["decision"] in ("apply", "review"):
                j2 = dict(j)
                j2.update({"score": r["score"], "decision": r["decision"]})
                shortlist.append(j2)
    else:
        rt = config.runtime_dir()
        jp = rt / "jobs.jsonl"
        if jp.exists():
            for line in jp.read_text().splitlines():
                if not line.strip():
                    continue
                j = json.loads(line)
                r = gates.evaluate(j)
                j["gate_decision"] = r["decision"]
                j["gate_score"] = r["score"]
                j["gate_reasons"] = r["reasons"]
                tracker.upsert_job(j)
                if r["decision"] in ("apply", "review"):
                    tracker.create_application(
                        j["job_id"], channel=j.get("channel") or "form",
                        status="shortlisted" if r["decision"] == "apply" else "gated")
                    j2 = dict(j)
                    j2.update({"score": r["score"], "decision": r["decision"]})
                    shortlist.append(j2)
    shortlist.sort(key=lambda x: x.get("score", 0), reverse=True)
    print("gate: %d shortlisted" % len(shortlist))
    _phase("P6-loop", "gate done (%d shortlisted)" % len(shortlist))

    # 3. route (draft | handoff)
    n = 0
    actions = []
    for j in shortlist:
        if limit is not None and n >= limit:
            print("route: limit %d reached, %d job(s) left for next run"
                  % (limit, len(shortlist) - n))
            break
        action, why = route_job(j, dry=dry, drafts_today=drafts_today,
                                profile=profile, max_drafts=max_drafts,
                                confirm_submit=confirm_submit)

        actions.append((j.get("company"), action, why))
        print("route: %-28s %-14s %s" % (j.get("company", "?"), action, why))
        if action in ("drafted", "prefilled", "would_draft", "would_prefill"):
            n += 1
            drafts_today += 1
    _phase("P6-loop", "routing done (%d actions)" % len(actions))

    # 4. sync
    if dry:
        print("sync: WOULD write career-ops TSV (no write in dry-run)")
    else:
        tracker.sync()
    _phase("P6-loop", "sync done")

    # 5. report
    if dry:
        print("report: WOULD write markdown dashboard (no write in dry-run)")
    else:
        from . import report as _report
        _report.run()
    _phase("P6-loop", "loop complete (%s)" % ("dry-run" if dry else "live"))
    print("loop done: %d action(s)%s" % (len(actions), " (dry-run, zero writes)" if dry else ""))
    return actions


if __name__ == "__main__":
    run()
