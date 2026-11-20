"""Batch apply: measured, gated, audited form submissions.

Authorization is per run and never comes from config: `--confirm-submit` (the
second key) plus an explicit `--max-submissions` override of
`policy.max_submissions_per_day` (default 0, which refuses everything). The
committed config stays safe for anyone else running the loop, and the run says
loudly what it overrode.

Only the form channel can submit. An email-channel job yields a Gmail draft the
candidate sends themselves (ja/gmail_draft.py has no send endpoint by design),
so it is counted and reported separately and never counted as an application.

Every submission is decided by `submit_verdict`, a pure function of one
`prepare_application` result, evaluated inside the page at the point where the
click happens (see ja/channels/form.py). The two-key guard there stays in force:
this gate can only ever refuse more, never less.
"""
import json
import os
import urllib.error
import urllib.request
from datetime import date, datetime
from pathlib import Path

from . import config, gates, priority, tracker
from .channels import form as fm
from .loop import _record_intent, already_drafted, caps_used

CONFIDENCE_MIN = 90
SUBMITTED_BY = "candidate-authorized-run"


def submit_verdict(result):
    """(allowed, reason) for one prepare_application result. Pure; never raises.

    Every condition is a hard stop. Order is chosen so the reported reason is
    the most specific one: walls first, then blank required fields, then the
    confidence number.
    """
    if not isinstance(result, dict):
        return False, "no result to judge"
    stopped = result.get("stopped")
    if stopped:
        return False, "run stopped: %s" % stopped
    if result.get("needs_human_captcha"):
        return False, "captcha not cleared after 3 attempts"
    ts = result.get("turnstile")
    if isinstance(ts, dict) and ts.get("ok") is False:
        return False, "turnstile unsolved: %s" % (ts.get("error") or "unknown")
    if result.get("password_fields"):
        return False, "login/auth wall: the page asks for a password"
    blocked = result.get("submit_blocked_reason")
    if blocked:
        return False, blocked
    conf = result.get("confidence") if isinstance(result.get("confidence"), dict) else {}
    rows = conf.get("fields") if isinstance(conf.get("fields"), list) else []
    required = {r.get("name") for r in rows if isinstance(r, dict) and r.get("required")}
    # a file input that really uploaded is filled, whatever needs_human says
    done_file = {r.get("name") for r in rows
                 if isinstance(r, dict) and r.get("state") == "uploaded"}
    stuck = []
    for nh in result.get("needs_human") or []:
        if not isinstance(nh, dict):
            continue
        key = nh.get("field")
        if key in required and key not in done_file and key not in stuck:
            stuck.append(key)
    if stuck:
        return False, "required field still needs a human: %s" % ", ".join(stuck[:5])
    pct = conf.get("pct")
    if pct is None:
        return False, "no measurable confidence (nothing on the page to score)"
    if int(pct) < CONFIDENCE_MIN:
        return False, "confidence %s%% < %d%%" % (pct, CONFIDENCE_MIN)
    return True, "confidence %s%% >= %d%% and nothing required is blank" % (pct, CONFIDENCE_MIN)


def is_wall(result):
    """True when the page blocked us rather than answering us wrongly."""
    if not isinstance(result, dict):
        return False
    ts = result.get("turnstile")
    return bool(result.get("needs_human_captcha")
                or result.get("password_fields")
                or (isinstance(ts, dict) and ts.get("ok") is False))


def confirmation_signals(result):
    """What proves the page accepted it: body signals, or a URL that moved away."""
    conf = result.get("confirmation") if isinstance(result.get("confirmation"), dict) else {}
    signals = [s for s in (conf.get("signals") or []) if s]
    before = result.get("apply_url")
    after = conf.get("url")
    moved = bool(before and after and after != before)
    return {"signals": signals, "url": after, "url_moved": moved,
            "confirmed": bool(signals) or moved}


def resolve_cap(requested, config_value=None):
    """(effective cap, overridden). --max-submissions overrides the config value."""
    cfg = int(config.get("policy.max_submissions_per_day", 0)) if config_value is None \
        else int(config_value)
    if requested is None:
        return cfg, False
    return int(requested), int(requested) != cfg


POOL_SUPPLY = "supply*.jsonl"  # the gate-result pool (it has been renamed before)

# Cheapest possible liveness check, before any browser starts. Two pages that
# reached a browser on 2026-11-20 were HTTP 404 SPA shells (Andela Staff
# Fullstack, ConsenSys SRE I) that measured pct=None for 15 s of Chromium each.
# Liveness is judged by status, never by input count: a live Ashby posting
# returns 200 with zero <input> elements because the form hydrates client-side.
LIVENESS_TIMEOUT = 8.0
DEAD_STATUS = (404, 410)
WALL_STATUS = (403, 429)
_UA = "Mozilla/5.0 (compatible; job-autopilot liveness check)"


def liveness(url, timeout=None, opener=None):
    """(alive, reason, status) for one apply url. Never raises.

    404/410 is a dead posting; 403/429 is a wall in front of a live one, counted
    separately because it is a different problem. Every other outcome — 5xx, a
    timeout, a DNS blip, no url at all — is deliberately treated as ALIVE: a
    transient network failure must never drop a candidate.
    """
    if not url:
        return True, "no apply url to check (not judged dead)", None
    op = opener or urllib.request.urlopen
    req = urllib.request.Request(url, headers={"User-Agent": _UA}, method="GET")
    try:
        r = op(req, timeout=timeout or LIVENESS_TIMEOUT)
        return True, "http %s" % getattr(r, "status", 200), getattr(r, "status", 200)
    except urllib.error.HTTPError as e:
        if e.code in DEAD_STATUS:
            return False, "dead posting (http %d)" % e.code, e.code
        if e.code in WALL_STATUS:
            return False, "wall on the apply url (http %d), not a dead posting" % e.code, e.code
        return True, "http %d (transient, not judged dead)" % e.code, e.code
    except Exception as e:
        return True, "%s: not judged dead" % type(e).__name__, None


def _liveness_probe(timeout=None):
    """A per-run URL cache in front of liveness(): one GET per distinct apply_url."""
    seen = {}

    def probe(url):
        if url not in seen:
            seen[url] = liveness(url, timeout=timeout)
        return seen[url]

    return probe


def _load_pool():
    """Job records by job_id, plus how many rows each file contributed.

    `<runtime>/supply*.jsonl` is the gate-result pool the shortlist was built
    from; `jobs.jsonl` is whatever the last `discover` wrote, so it is read last
    and wins.
    """
    pool, sources = {}, {}
    paths = sorted(config.runtime_dir().glob(POOL_SUPPLY))
    paths.append(config.runtime_dir() / "jobs.jsonl")
    for p in paths:
        if not p.exists():
            continue
        n = 0
        for line in p.read_text().splitlines():
            if not line.strip():
                continue
            try:
                j = json.loads(line)
            except Exception:
                continue
            if j.get("job_id"):
                pool[j["job_id"]] = j
                n += 1
        sources[p.name] = n
    return pool, sources


def _load_shortlist():
    from .__main__ import _shortlist_from_disk  # one source for the ranked pool
    return _shortlist_from_disk()


def _engineering_rank(job):
    """The runner's own invariant: rank 3 means "not an engineering family".

    A rank-3 role reaching the runner is a selection bug, not a skip.
    """
    rank = priority.role_rank(job)
    assert rank <= 2, "rank-3 (non-engineering) role reached the runner: %r" % (
        (job or {}).get("role"),)
    return rank


def select_candidates(shortlist, pool, attempts, decisions=("apply",), liveness_probe=None):
    """Best-first form candidates: freshly gated, engineering, one company.

    A stored decision is a cache with no invalidation key: ja/gates.py changes
    (the developing-country onsite rule, structured locations next) invalidate
    every decision already sitting in shortlist.json. So the gates are re-run
    here, on the assembled record, and the stored value is kept beside it as
    `stored_decision` so drift is visible in the report and counted in the tally.
    `liveness_probe` (optional) is the last, cheapest-first step: one GET per
    distinct apply_url, refusing 404/410 before a browser is launched.
    """
    wanted = {d.strip().lower() for d in decisions}
    tally, cands, seen = {}, [], {}
    for entry in shortlist:
        jid = entry.get("job_id")
        job = dict(entry)
        job.update({k: v for k, v in (pool.get(jid) or {}).items() if v is not None})
        channel = (job.get("channel") or "form").lower()
        if channel != "form":
            tally["email channel: draft the candidate sends, not an application"] = \
                tally.get("email channel: draft the candidate sends, not an application", 0) + 1
            continue
        stored = (entry.get("decision") or job.get("decision") or "").lower()
        if priority.role_rank(job) > 2:
            tally["not an engineering role (rank 3)"] = \
                tally.get("not an engineering role (rank 3)", 0) + 1
            continue
        if jid not in pool:
            # the pool belongs to another workstream and gets rewritten; the
            # shortlist entry alone carries company, role and url, so a missing
            # row costs enrichment (ats/apply_url), never the candidate
            tally["pool had no row (shortlist only)"] = \
                tally.get("pool had no row (shortlist only)", 0) + 1
        if not (job.get("apply_url") or job.get("url")):
            tally["no apply url"] = tally.get("no apply url", 0) + 1
            continue
        if jid in seen:
            fresh = seen[jid]
        else:
            fresh = gates.evaluate(job) or {}
            seen[jid] = fresh
        decision = (fresh.get("decision") or "").lower()
        if stored and decision and stored != decision:
            drift = "stale decision: stored %s, now %s" % (stored, decision)
            tally[drift] = tally.get(drift, 0) + 1
        job["stored_decision"] = stored
        job["stored_score"] = job.get("score")
        job["decision"] = decision or stored
        job["score"] = fresh.get("score")
        job["reasons"] = fresh.get("reasons") or job.get("reasons")
        if (job["decision"] or "") not in wanted:
            tally["gate decision %s (re-evaluated, not in --decision)" % (job["decision"] or "unrecorded")] = \
                tally.get("gate decision %s (re-evaluated, not in --decision)"
                          % (job["decision"] or "unrecorded"), 0) + 1
            continue
        # our own unsubmitted attempt (a refused prefill) is not a duplicate of
        # itself: reuse its row so the page stays retryable, and keep excluding
        # everything a human or a real submission would have created
        aid = tracker.retryable_attempt(jid, "form")
        blocked = already_drafted(jid, channel="form")
        if blocked and not (aid and blocked in ("drafted", "prefilled")):
            tally["already applied on form"] = tally.get("already applied on form", 0) + 1
            continue
        if fm._duplicate(job.get("company") or "", job.get("role") or "", "form",
                         exclude_application_id=aid):
            tally["duplicate company+role"] = tally.get("duplicate company+role", 0) + 1
            continue
        if aid:
            job["application_id"] = aid
        if liveness_probe is not None:
            url = job.get("apply_url") or job.get("url")
            alive, why, status = liveness_probe(url)
            if not alive:
                tally[why] = tally.get(why, 0) + 1
                continue
            job["liveness"] = {"status": status, "note": why}
        cands.append(job)
    picked = priority.pick_per_company(cands)
    if len(cands) > len(picked):
        tally["same company, another role"] = len(cands) - len(picked)
    ordered = sorted(picked.values(), key=lambda j: -(j.get("score") or 0))
    return ordered[:attempts], tally


def _artifact_dir(result):
    p = (result.get("artifacts") or {}).get("filled_json")
    return Path(p).parent if p else None


def write_evidence(adir, payload):
    """submit_evidence.json, same tmp + os.replace discipline as the sidecars."""
    p = Path(adir) / "submit_evidence.json"
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    os.replace(tmp, p)
    return str(p)


def report_path():
    return config.runtime_dir() / "reports" / ("batch-apply-%s.jsonl" % date.today().isoformat())


def write_report(line, path=None):
    """One JSON line per attempt: 50 attempts have to be auditable afterwards."""
    p = Path(path) if path else report_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(line, ensure_ascii=False) + "\n")
    return str(p)


def run_batch(limit=5, attempts=None, max_submissions=None, dry=False, confirm_submit=False,
              decisions=("apply",), continue_on_wall=False, only=None):
    """Select -> prefill -> gate -> submit -> evidence, with hard batch stops.

    Returns a summary dict. `limit` bounds confirmed submissions; `attempts`
    bounds browser prefills (default 4x limit) so a live batch cannot walk the
    whole pool. `decisions` picks the gate bands to draw from ("apply" only by
    default; "review" is the ~1,875-job band the gates left to a human).
    A captcha/auth wall stops the batch by default (`out["stopped"]`);
    `continue_on_wall` only keeps a measurement run walking instead.
    `only` (job ids) scopes the run to candidates already cleared by hand: the
    selection still runs, the scope is applied after it, and the log says how many
    it dropped, so a live batch can never wander onto an unscreened page.
    """
    cap, overridden = resolve_cap(max_submissions)
    cfg_cap = int(config.get("policy.max_submissions_per_day", 0))
    cfg_allow = config.allow_automated_submit()
    subs_today = caps_used()[1]
    allow_submit = bool(confirm_submit) and not dry and cap > 0

    print("batch-apply: %s | submissions limit=%d | prefill attempts=%d"
          % ("dry-run (no submit)" if dry else "live", limit, attempts))
    print("submission cap: effective=%d%s | already today=%d"
          % (cap, (" (--max-submissions override; config says %d)" % cfg_cap)
             if overridden else " (config policy.max_submissions_per_day)", subs_today))
    if not cap:
        print("  cap 0 = no submission authorized; pass --max-submissions N to authorize this run")
    if allow_submit and not cfg_allow:
        print("  submit guard: config policy.allow_automated_submit=false; this run passes "
              "allow_submit=True explicitly (config file untouched)")
    if not allow_submit:
        print("  two-key rule: no submit key for this run (%s) -> measurement only"
              % ("dry-run" if dry else "--confirm-submit missing or cap 0"))

    profile = config.profile() or {}
    if not dry:
        tracker.init_db()
    pool, sources = _load_pool()
    print("pool: %d job records (%s)" % (len(pool), ", ".join(
        "%s %d" % (k, v) for k, v in sources.items())))
    candidates, tally = select_candidates(_load_shortlist(), pool, attempts, decisions,
                                          liveness_probe=_liveness_probe())
    email_n = tally.get("email channel: draft the candidate sends, not an application", 0)
    print("decisions accepted: %s | candidates: %d selected of %d shortlisted | "
          "email-channel jobs left to `ja draft`: %d"
          % (",".join(decisions), len(candidates), sum(tally.values()), email_n))
    for reason, n in sorted(tally.items(), key=lambda kv: -kv[1]):
        print("  left out: %-62s %d" % (reason, n))

    if only:
        wanted = {str(x).strip() for x in only if str(x).strip()}
        before = len(candidates)
        candidates = [c for c in candidates if c.get("job_id") in wanted]
        print("only: %d of %d selected candidates (%s)%s"
              % (len(candidates), before, ",".join(sorted(wanted)),
                 "" if len(candidates) == len(wanted) else
                 " | MISSING: " + ",".join(sorted(wanted - {c.get("job_id") for c in candidates}))))

    out = {"selected": len(candidates), "submitted": 0, "unconfirmed": 0, "refused": 0,
           "would_submit": 0, "dry": dry, "effective_cap": cap, "cap_overridden": overridden,
           "submissions_before": subs_today, "email_candidates": email_n, "stopped": None,
           "attempts": 0, "allow_submit": allow_submit, "walls": 0}
    consecutive_unconfirmed = 0

    for i, job in enumerate(candidates, 1):
        if out["submitted"] >= limit:
            out["stopped"] = "limit reached (%d submissions)" % limit
            break
        if allow_submit and subs_today + out["submitted"] >= cap:
            out["stopped"] = "daily cap reached (max_submissions_per_day=%d)" % cap
            break
        jid = job.get("job_id")
        _engineering_rank(job)  # a rank-3 role here is a selection bug
        rec = {"ts": datetime.now().isoformat(timespec="seconds"), "run": "batch-apply",
               "attempt": i, "dry": dry, "job_id": jid, "company": job.get("company"),
               "role": job.get("role"), "channel": "form",
               "gate_decision": job.get("decision"), "gate_score": job.get("score"),
               "stored_decision": job.get("stored_decision"),
               "stored_score": job.get("stored_score"),
               "apply_url": job.get("apply_url") or job.get("url"),
               "liveness": job.get("liveness"),
               "effective_cap": cap, "cap_overridden": overridden, "allow_submit": allow_submit}
        if dry:
            tracker.record_event("submit_planned_dry", job_id=jid,
                                 detail={"channel": "form", "company": job.get("company"),
                                         "role": job.get("role"), "dry_run": True})
        else:
            _record_intent("submit_intent", jid,
                           {"channel": "form", "company": job.get("company"),
                            "role": job.get("role"), "allow_submit": allow_submit,
                            "max_submissions": cap})
        try:
            res = fm.prepare_application(job, profile, allow_submit=allow_submit,
                                         confirm_submit=bool(confirm_submit), gate=submit_verdict)
        except Exception as e:  # a browser/ATS explosion is not a submission
            rec.update({"outcome": "error", "error": str(e)})
            write_report(rec)
            out["attempts"] += 1
            print("  [%d] %s | %s -> ERROR %s" % (i, job.get("company"), job.get("role"), e))
            continue
        out["attempts"] += 1

        verdict = res.get("gate") if isinstance(res.get("gate"), dict) else {}
        allowed = bool(verdict.get("allowed"))
        reason = verdict.get("reason") or submit_verdict(res)[1]
        conf = res.get("confidence") or {}
        sig = confirmation_signals(res)
        submitted = bool(res.get("submitted"))
        if submitted and sig["confirmed"]:
            outcome = "submitted"
        elif submitted:
            outcome = "unconfirmed"
        elif allowed and not allow_submit:
            outcome = "would_submit"
        else:
            outcome = "refused"
        adir = _artifact_dir(res)

        if not dry:
            stopped = res.get("stopped")
            if stopped:
                note = "run stopped: %s" % stopped
                aid = tracker.create_application(jid, channel="form", status="withdrawn",
                                                 notes=note)
                tracker.record_event("application_withdrawn", job_id=jid,
                                     application_id=aid, detail={"reason": note})
            else:
                aid = tracker.create_application(jid, channel="form", status="prefilled",
                                                 notes=reason if outcome == "refused" else None)
                if outcome == "submitted":
                    tracker.mark_submitted(aid, SUBMITTED_BY,
                                           notes="auto-submitted by batch-apply "
                                                 "(user-authorized run, gate: %s)" % reason,
                                           job_id=jid)
                tracker.record_event("submit_done" if outcome == "submitted" else "prefill_done",
                                     job_id=jid, application_id=aid,
                                     detail={"outcome": outcome, "pct": conf.get("pct"),
                                             "reason": reason,
                                             "confirmed": sig["confirmed"]})

        rec.update({"ats": res.get("ats"), "pct": conf.get("pct"),
                    "unresolved_required": conf.get("unresolved_required"),
                    "skipped_optional": conf.get("skipped_optional"),
                    "gate": {"allowed": allowed, "reason": reason},
                    "outcome": outcome, "submitted": submitted,
                    "confirmed": sig["confirmed"], "confirmation_signals": sig["signals"],
                    "url_after": sig["url"], "url_moved": sig["url_moved"],
                    "artifact_dir": str(adir) if adir else None,
                    "needs_human": len(res.get("needs_human") or []),
                    "submit_blocked_reason": res.get("submit_blocked_reason"),
                    "filled_json": (res.get("artifacts") or {}).get("filled_json"),
                    "confidence_json": (res.get("artifacts") or {}).get("confidence_json"),
                    "submitted_screenshot": (res.get("artifacts") or {}).get("submitted_screenshot")})
        if adir:
            rec["submit_evidence_json"] = write_evidence(adir, {
                "job_id": jid, "company": job.get("company"), "role": job.get("role"),
                "apply_url": rec["apply_url"], "ats": res.get("ats"), "dry": dry,
                "outcome": outcome, "gate": {"allowed": allowed, "reason": reason},
                "gate_decision": job.get("decision"), "gate_score": job.get("score"),
                "stored_decision": job.get("stored_decision"),
                "stored_score": job.get("stored_score"),
                "pct": conf.get("pct"), "submitted": submitted,
                "confirmed": sig["confirmed"], "confirmation": res.get("confirmation"),
                "submitted_screenshot": (res.get("artifacts") or {}).get("submitted_screenshot"),
                "submitted_by": SUBMITTED_BY if outcome == "submitted" else None,
                "allow_submit": allow_submit, "effective_cap": cap,
            })
        write_report(rec)
        out[outcome] += 1

        print("  [%d] %s | %s -> %s pct=%s%s %s"
              % (i, job.get("company"), job.get("role"), outcome, conf.get("pct"),
                 " signals=" + ",".join(sig["signals"]) if sig["signals"] else "",
                 "" if allowed else "| " + str(reason)))

        if submitted and not sig["confirmed"]:
            consecutive_unconfirmed += 1
            if consecutive_unconfirmed >= 2:
                out["stopped"] = ("2 consecutive submissions with no confirmation signal "
                                  "(page accepted the click but showed nothing)")
                break
        else:
            consecutive_unconfirmed = 0
        if not allowed and is_wall(res):
            out["walls"] += 1
            if continue_on_wall:
                # measurement run: a wall says nothing about the next page
                print("    wall %d: continuing past %s" % (out["walls"], reason))
                continue
            out["stopped"] = "page wall: " + str(reason)
            break

    if out["stopped"]:
        print("BATCH STOPPED: %s" % out["stopped"])
    print("batch-apply done: submitted=%d (confirmed) unconfirmed=%d refused=%d would_submit=%d "
          "of %d attempts (walls=%d)" % (out["submitted"], out["unconfirmed"], out["refused"],
                                         out["would_submit"], out["attempts"], out["walls"]))
    print("submission budget: %d used in this run, cap %d%s | email-channel jobs are drafts the "
          "candidate sends and are never counted as applications (%d waiting on `ja draft`)"
          % (out["submitted"], cap, " (override)" if overridden else "", email_n))
    print("report: %s" % report_path())
    return out
