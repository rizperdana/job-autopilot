"""CLI entry point: python3 -m ja ...

Subcommands: doctor selftest discover gate draft apply sync stats status report loop.
Every command supports --dry-run. draft never sends; apply never auto-submits
(two-key rule) -- the boundary is a Gmail draft / prefill handoff.
"""
import argparse
import json
import sys
from datetime import date

from . import config
from . import sources
from . import gates
from . import tracker
from . import state
from . import report as report_mod


def _atomic_write_text(path, text):
    """Temp file + os.replace: interrupted runs cannot leave a half-parsed file."""
    import os
    path = str(path)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def _dry(args):
    return bool(getattr(args, "dry_run", False))


def cmd_doctor(args):
    print("python:", sys.version.split()[0])
    print("config paths:")
    print("  career_ops:", config.career_ops_dir())
    print("  runtime:", config.runtime_dir())
    print("  dataset:", config.dataset_path())
    print("  db:", tracker.db_path())
    print("  profile:", config.profile_path(), "exists=", config.profile_path().exists())
    print("  cv:", config.cv_path(), "exists=", config.cv_path().exists())
    ds = config.dataset_path()
    for f in ("dataset.jsonl", "liveness.jsonl", "pass2.jsonl"):
        p = ds / f
        print("  ", f, "exists=", p.exists(), ("size=" + str(p.stat().st_size) if p.exists() else ""))
    print("  portals.yml:", (config.career_ops_dir() / "portals.yml").exists())
    print("  config ja.yml:", (config.ROOT / "config" / "ja.yml").exists(),
          "(example only)" if not (config.ROOT / "config" / "ja.yml").exists() else "")
    print("  gmail token store:", __import__("pathlib").Path("~/.mcp-z/tokens.json").expanduser(),
          "exists=", __import__("pathlib").Path.home().joinpath(".mcp-z/tokens.json").exists())


def cmd_selftest(args):
    import unittest
    loader = unittest.TestLoader()
    suite = loader.discover("tests", pattern="test_*.py")
    runner = unittest.TextTestRunner(verbosity=2)
    res = runner.run(suite)
    sys.exit(0 if res.wasSuccessful() else 1)


def cmd_discover(args):
    dry = _dry(args)
    jobs = sources.run() if not dry else _discover_dry()
    if not dry:
        tracker.init_db()
        for j in jobs:
            tracker.upsert_job(j)
            tracker.create_application(j["job_id"], channel=j.get("channel") or "form",
                                       status="discovered", dry_run=dry)
    print("discover done:", len(jobs), "jobs" + (" (dry-run, no db writes)" if dry else ""))


def _discover_dry():
    # dry-run: normalize without touching runtime jobs.jsonl. Fails loudly like
    # sources.run() -- never silently returns zero on a missing dataset.
    import io, contextlib
    base = config.dataset_path()
    ds_path = base / "dataset.jsonl"
    if not base.is_dir():
        raise SystemExit("discover: dataset dir missing: %s (expected %s). "
                         "Set paths.dataset or re-run the dataset extraction."
                         % (base, "dataset.jsonl, liveness.jsonl, pass2.jsonl"))
    if not ds_path.exists():
        raise SystemExit("discover: dataset.jsonl missing at %s (expected %s)"
                         % (ds_path, "dataset.jsonl, liveness.jsonl, pass2.jsonl"))
    with contextlib.redirect_stdout(io.StringIO()):
        return sources.run()


def cmd_gate(args):
    dry = _dry(args)
    if not dry:
        tracker.init_db()
    rt = config.runtime_dir()
    jobs_path = rt / "jobs.jsonl"
    if not jobs_path.exists():
        print("no jobs.jsonl; run discover first")
        return
    jobs = [json.loads(l) for l in jobs_path.read_text().splitlines() if l.strip()]
    shortlist = []
    hist = {}
    for j in jobs:
        r = gates.evaluate(j)
        j["gate_decision"] = r["decision"]
        j["gate_score"] = r["score"]
        j["gate_reasons"] = r["reasons"]
        if not dry:
            tracker.upsert_job(j)
            if r["decision"] == "reject_hard" and r.get("failed_gate"):
                tracker.record_rejection(j["job_id"], r["failed_gate"],
                                         "; ".join(r["reasons"]), dry_run=dry)
            if r["decision"] in ("apply", "review"):
                tracker.create_application(j["job_id"], channel=j.get("channel") or "form",
                                           status="shortlisted" if r["decision"] == "apply" else "gated",
                                           dry_run=dry)
        if r["decision"] in ("apply", "review", "skip"):
            hist[r["decision"]] = hist.get(r["decision"], 0) + 1
        if r["decision"] == "reject_hard":
            g = r.get("failed_gate") or "unknown"
            hist[g] = hist.get(g, 0) + 1
        if r["decision"] in ("apply", "review"):
            shortlist.append({
                "job_id": j["job_id"], "company": j["company"], "role": j["role"],
                "score": r["score"], "decision": r["decision"],
                "channel": j.get("channel") or "form",
                "url": j.get("apply_url") or j.get("url"),
                "reasons": r["reasons"]})
    shortlist.sort(key=lambda x: x["score"], reverse=True)
    if not dry:
        _atomic_write_text(rt / "shortlist.json", json.dumps(
            {"shortlist": shortlist, "histogram": hist}, indent=2))
    print("gated", len(jobs), "jobs" + (" (dry-run, no writes)" if dry else ""))
    print("decisions:", hist)
    print("shortlisted:", len(shortlist))


def _shortlist_from_disk():
    rt = config.runtime_dir()
    p = rt / "shortlist.json"
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text())
    except Exception:
        return []
    return data.get("shortlist") or []


def cmd_draft(args):
    """Create Gmail drafts for shortlisted jobs. NEVER sends."""
    dry = _dry(args)
    limit = getattr(args, "limit", None) or 5
    if dry:
        rows = _shortlist_from_disk()
        print("draft (dry-run): would create Gmail draft for", min(limit, len(rows)),
              "of", len(rows), "shortlisted job(s); never sends")
        for j in rows[:limit]:
            print("  WOULD draft:", j.get("company"), "|", j.get("role"),
                  "|", j.get("url"))
        return
    tracker.init_db()
    from .loop import route_job
    profile = config.profile() or {}
    max_drafts = config.max_drafts_per_day()
    drafts_today, _ = __import__("ja.loop", fromlist=["caps_used"]).caps_used()
    made = 0
    for j in _shortlist_from_disk():
        if made >= limit:
            break
        if drafts_today >= max_drafts:
            print("cap reached: max_drafts_per_day=%d" % max_drafts)
            break
        job = {"job_id": j["job_id"], "company": j["company"], "role": j["role"],
               "channel": j.get("channel") or "email",
               "url": j.get("url"), "apply_url": j.get("url")}
        action, why = route_job(job, dry=False,
                                drafts_today=drafts_today, profile=profile,
                                max_drafts=max_drafts, confirm_submit=False)
        print("draft:", j.get("company"), action, "--", why)
        if action == "drafted":
            made += 1
            drafts_today += 1
    print("draft done:", made, "draft(s)")

def cmd_apply(args):
    """Form channel: prefill + one-click handoff. NEVER auto-submits (two-key rule)."""
    dry = _dry(args)
    limit = getattr(args, "limit", None) or 5
    form_rows = [j for j in _shortlist_from_disk()
                 if (j.get("channel") or "form") == "form"]
    if dry:
        print("apply (dry-run): would prefill", min(limit, len(form_rows)),
              "of", len(form_rows), "form job(s); no submit (two-key rule)")
        for j in form_rows[:limit]:
            print("  WOULD prefill:", j.get("company"), "|", j.get("role"),
                  "|", j.get("url"))
        return
    tracker.init_db()
    from .loop import route_job
    profile = config.profile() or {}
    allow_cfg = config.allow_automated_submit()
    confirm = bool(getattr(args, "confirm_submit", False))
    if allow_cfg and not confirm:
        print("two-key rule: allow_automated_submit=true but confirm_submit missing -> blocked")
    if not allow_cfg:
        print("submit guard: allow_automated_submit=false -> prefill + handoff only")
    prefilled = 0
    for j in form_rows:
        if prefilled >= limit:
            break
        job = {"job_id": j["job_id"], "company": j["company"], "role": j["role"],
               "channel": "form", "url": j.get("url"), "apply_url": j.get("url")}
        action, why = route_job(job, dry=False, drafts_today=0, profile=profile,
                                max_drafts=config.max_drafts_per_day(),
                                confirm_submit=confirm)
        print("apply:", j.get("company"), action, "--", why)
        if action == "prefilled":
            prefilled += 1
    print("apply done:", prefilled, "handoff(s)")


def cmd_status(args):
    tracker.init_db()
    tracker.set_status(args.id, args.status, dry_run=_dry(args))


def cmd_stats(args):
    tracker.init_db()
    st = tracker.stats()
    print(json.dumps(st, indent=2, default=str))


def cmd_sync(args):
    dry = _dry(args)
    tracker.init_db()
    if dry:
        print("sync (dry-run): would write career-ops TSV to",
              config.career_ops_dir() / "batch" / "tracker-additions")
        return
    tracker.sync()


def cmd_report(args):
    dry = _dry(args)
    if dry:
        print("report (dry-run): would write markdown dashboard to",
              config.runtime_dir() / "reports")
        return
    report_mod.run()


def cmd_loop(args):
    from . import loop
    loop.run(limit=args.limit, dry=_dry(args),
             confirm_submit=bool(getattr(args, "confirm_submit", False)))


def main():
    ap = argparse.ArgumentParser(prog="ja")
    ap.add_argument("--dry-run", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add(name, fn, *extra):
        p = sub.add_parser(name)
        p.add_argument("--dry-run", action="store_true")
        p.set_defaults(func=fn)
        return p

    for name, fn in [("doctor", cmd_doctor), ("selftest", cmd_selftest),
                     ("discover", cmd_discover), ("gate", cmd_gate),
                     ("stats", cmd_stats), ("sync", cmd_sync), ("report", cmd_report)]:
        add(name, fn)

    p = add("draft", cmd_draft)
    p.add_argument("--limit", type=int, default=5)
    p = add("apply", cmd_apply)
    p.add_argument("--limit", type=int, default=5)
    p.add_argument("--confirm-submit", action="store_true")
    p = add("loop", cmd_loop)
    p.add_argument("--limit", type=int, default=5)
    p = add("status", cmd_status)
    p.add_argument("id")
    p.add_argument("status")

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
