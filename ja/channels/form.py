"""Form channel — Playwright-driven ATS form fill with handoff (no auto-submit by default)."""
import json
import os
import re
import time
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

from .. import config, tracker

ATS_HOSTS = [
    ("greenhouse", ["greenhouse.io", "boards.greenhouse.io"]),
    ("lever", ["lever.co", "jobs.lever.co"]),
    ("ashby", ["ashbyhq.com", "jobs.ashbyhq.com"]),
    ("workable", ["workable.com", "apply.workable.com"]),
    ("smartrecruiters", ["smartrecruiters.com"]),
    ("workday", ["workday.com", "myworkdayjobs.com"]),
    ("recruitee", ["recruitee.com"]),
    ("teamtailor", ["teamtailor.com"]),
    ("personio", ["personio.com", "personio.de"]),
    ("bamboohr", ["bamboohr.com"]),
    ("breezy", ["breezy.hr"]),
    ("jazzhr", ["jazzhr.com", "applytojob.com"]),
    ("icims", ["icims.com"]),
    ("jobvite", ["jobvite.com"]),
    ("gupy", ["gupy.io"]),
]

NEEDS_HUMAN = "NEEDS_HUMAN"

def detect_ats(url):
    host = (urlparse(url).hostname or "").lower()
    for name, domains in ATS_HOSTS:
        for d in domains:
            if host == d or host.endswith("." + d) or d in host:
                return name
    return "generic"

def _runtime():
    return config.runtime_dir()

def _blacklisted(company):
    p = config.career_ops_dir() / "data" / "blacklist.md"
    if not p.exists():
        return False
    try:
        text = p.read_text().lower()
    except Exception:
        return False
    return company.strip().lower() in text

def _duplicate(company, role):
    try:
        conn = None
        import sqlite3
        db = tracker.db_path()
        if not db.exists():
            return False
        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT j.job_id FROM jobs j JOIN applications a ON a.job_id=j.job_id "
            "WHERE lower(j.company)=? AND lower(j.role)=? LIMIT 1",
            (company.strip().lower(), role.strip().lower()),
        ).fetchone()
        conn.close()
        return row is not None
    except Exception:
        return False

def _profile_facts(profile):
    c = (profile.get("candidate") or {}) if isinstance(profile, dict) else {}
    comp = profile.get("compensation") or {}
    loc = profile.get("location") or {}
    return {
        "full_name": c.get("full_name"),
        "email": c.get("email"),
        "phone": c.get("phone"),
        "location": c.get("location") or (f"{loc.get('city','')}, {loc.get('country','')}".strip(", ")),
        "linkedin": c.get("linkedin"),
        "github": c.get("github"),
        "portfolio": c.get("portfolio_url"),
        "current_company": None,
        "years_experience": None,  # never invent; only set if a verbatim fact exists
        "work_authorized": "Indonesia" if (loc.get("country") == "Indonesia") else None,
        "needs_sponsorship": (profile.get("location") or {}).get("needs_sponsorship"),
        "salary": comp.get("target_range") or comp.get("minimum"),
        "how_heard": "job board",
    }

def _answer_bank(path):
    if not path:
        return {}
    p = Path(path)
    if not p.exists():
        return {}
    try:
        import yaml
    except Exception:
        yaml = None
    text = p.read_text()
    if yaml is not None:
        try:
            d = yaml.safe_load(text)
            if isinstance(d, dict):
                return d
        except Exception:
            pass
    # minimal key: value parser
    out = {}
    for line in text.splitlines():
        if ":" in line and not line.lstrip().startswith("#"):
            k, _, v = line.partition(":")
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out

def map_fields(fields, profile, answers=None):
    """Pure function: list of form field descriptors -> {field_key: answer}.

    Matching is keyword/type-driven (never positional). Unknown or
    contradictory values become NEEDS_HUMAN with a recorded reason in the
    returned dict? Reasons are tracked separately via map_fields_reasons.
    """
    facts = _profile_facts(profile or {})
    bank = answers or {}
    result = {}
    reasons = {}
    for i, f in enumerate(fields or []):
        label = (f.get("label") or f.get("name") or f.get("id") or f.get("placeholder") or "").strip()
        low = label.lower()
        name_low = (f.get("name") or "").lower()
        id_low = (f.get("id") or "").lower()
        ph_low = (f.get("placeholder") or "").lower()
        haystack = " ".join([low, name_low, id_low, ph_low])
        key = f.get("name") or f.get("id") or "field_%d" % i
        answer = NEEDS_HUMAN
        reason = None

        # name handling
        if any(w in haystack for w in ["company", "organization", "employer"]) and "current" not in haystack and "previous" not in haystack:
            # do not map person name to company/org fields
            pass
        elif "first name" in haystack or "given name" in haystack or "firstname" in haystack:
            if facts["full_name"]:
                answer = facts["full_name"].split()[0]
        elif "last name" in haystack or "family name" in haystack or "surname" in haystack or "lastname" in haystack:
            parts = (facts["full_name"] or "").split()
            answer = parts[-1] if len(parts) > 1 else NEEDS_HUMAN
        elif "customer name" in haystack or "full name" in haystack or "your name" in haystack or haystack.strip() in ("name",) or "applicant name" in haystack or "candidate name" in haystack:
            answer = facts["full_name"]
        elif "name" in haystack and "company" not in haystack and "organization" not in haystack:
            answer = facts["full_name"]

        elif "email" in haystack or "e-mail" in haystack or "mail" in haystack:
            answer = facts["email"]
        elif "phone" in haystack or "mobile" in haystack or "telephone" in haystack or "tel" in haystack:
            answer = facts["phone"]
        elif any(w in haystack for w in ["location", "city", "address", "where are you based", "current location", "country"]):
            answer = facts["location"]
        elif "linkedin" in haystack:
            answer = facts["linkedin"]
        elif "github" in haystack:
            answer = facts["github"]
        elif any(w in haystack for w in ["portfolio", "website", "personal site"]):
            answer = facts["portfolio"]
        elif "current company" in haystack or "current employer" in haystack:
            answer = facts["current_company"] if facts["current_company"] is not None else NEEDS_HUMAN
        elif "years" in haystack and ("experience" in haystack or "exp" in haystack):
            ye = facts["years_experience"]
            answer = ye if ye is not None else NEEDS_HUMAN
        elif any(w in haystack for w in ["work author", "authorized to work", "eligib", "right to work", "legally"]):
            answer = "Yes" if facts["work_authorized"] else NEEDS_HUMAN
        elif any(w in haystack for w in ["sponsorship", "visa sponsor", "require sponsor"]):
            answer = ("Yes" if facts["needs_sponsorship"] else "No") if facts["needs_sponsorship"] is not None else NEEDS_HUMAN
        elif any(w in haystack for w in ["salary", "compensation", "expected", "desired"]):
            answer = facts["salary"]
        elif any(w in haystack for w in ["how did you hear", "how hear", "source", "referral"]):
            answer = facts["how_heard"]
        elif any(w in haystack for w in ["cover letter", "why", "motivation", "additional", "anything else", "comments", "delivery instructions"]):
            for k, v in bank.items():
                if k.lower() in haystack or haystack in k.lower():
                    answer = v
                    break
            else:
                answer = NEEDS_HUMAN
        else:
            for k, v in bank.items():
                if k.lower() in haystack or haystack in k.lower():
                    answer = v
                    break
            else:
                answer = NEEDS_HUMAN

        if answer is None:
            answer = NEEDS_HUMAN

        # contradiction guard
        if answer != NEEDS_HUMAN:
            ok, r = _shape_ok(haystack, answer)
            if not ok:
                reasons[key] = r
                answer = NEEDS_HUMAN

        result[key] = answer

    # store reasons on a side channel via attribute on result? Instead attach to a module-level? 
    # We'll attach as result["_reasons"] removed before return? Tests may check. Keep separate:
    # attach to result under a key that real callers can ignore.
    result["_reasons"] = reasons
    return result

def _shape_ok(haystack, value):
    v = str(value).strip()
    low = haystack
    is_email_field = ("email" in low or "e-mail" in low or "mail" in low)
    is_phone_field = ("phone" in low or "mobile" in low or "telephone" in low or "tel" in low)
    is_number_field = ("years" in low or "amount" in low)
    if is_email_field:
        if "@" not in v or "." not in v.split("@")[-1]:
            return False, "value is not an email address"
    if is_phone_field and "@" in v:
        return False, "email-shaped value into phone field"
    if is_number_field and v and v != "NEEDS_HUMAN":
        try:
            float(v.replace("$","").replace("K","000").split("-")[0].strip().split()[0])
        except Exception:
            return False, "value is not numeric"
    return True, ""

def _resolve_cv(attach=None):
    """Resolve the CV attachment: explicit attach -> newest *.pdf under career-ops/output -> else error."""
    if attach:
        p = Path(attach).expanduser()
        if not p.is_file():
            raise FileNotFoundError("CV attachment not found: %s" % p)
        return p
    out_dir = config.career_ops_dir() / "output"
    if out_dir.exists():
        pdfs = sorted(out_dir.glob("*.pdf"), key=lambda p: p.stat().st_mtime, reverse=True)
        if pdfs:
            return pdfs[0]
    raise FileNotFoundError(
        "No CV PDF found: searched explicit --attach (none provided) and %s/*.pdf"
        % out_dir
    )


def _newest_cv(attach=None):
    return _resolve_cv(attach)

def _artifact_dir(job_id, artifacts_dir):
    if artifacts_dir:
        d = Path(artifacts_dir)
    else:
        d = _runtime() / "runs" / date.today().isoformat() / (job_id or "job")
    d.mkdir(parents=True, exist_ok=True)
    return d

def _write_json(p, obj):
    Path(p).write_text(json.dumps(obj, indent=2, ensure_ascii=False))

def prepare_application(job, profile, answers_path=None, allow_submit=False,
                       artifacts_dir=None, confirm_submit=False, headless=True,
                       timeout_ms=30000, attach=None, cv_path=None, cv=None):
    job = dict(job or {})
    job_id = job.get("job_id") or job.get("id") or "job"
    company = job.get("company", "")
    role = job.get("role", "")
    apply_url = job.get("apply_url") or job.get("url")
    adir = _artifact_dir(job_id, artifacts_dir)
    steps = []
    def log(step, **kw):
        steps.append({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "step": step, **kw})
        (adir / "steps.jsonl").write_text("".join(json.dumps(s) + "\n" for s in steps))

    result = {
        "job_id": job_id,
        "company": company,
        "role": role,
        "apply_url": apply_url,
        "ats": None,
        "stopped": None,
        "needs_human": [],
        "filled": {},
        "cv_used": None,
        "turnstile": None,
        "turnstile_attempts": 0,
        "needs_human_captcha": False,
        "ready_to_submit": False,
        "submit_selector": None,
        "actions": [],
        "artifacts": {},
    }

    # preflight: blacklist
    if _blacklisted(company):
        result["stopped"] = "blacklist"
        log("stopped", reason="blacklist")
        _finalize(result, adir, steps)
        return result
    # preflight: duplicate
    if _duplicate(company, role):
        result["stopped"] = "duplicate"
        log("stopped", reason="duplicate")
        _finalize(result, adir, steps)
        return result

    ats = detect_ats(apply_url)
    result["ats"] = ats
    log("detect_ats", ats=ats)

    # CV selection
    cv_arg = attach if attach is not None else (cv_path if cv_path is not None else cv)
    cv = _resolve_cv(cv_arg)
    result["cv_used"] = str(cv) if cv else None
    log("cv_selected", path=str(cv) if cv else None)

    # browser
    from .. import browser as B
    pw, ctx = B.launch(headless=headless, timeout_ms=timeout_ms)
    page = None
    try:
        page = ctx.new_page()
        page.goto(apply_url, wait_until="domcontentloaded", timeout=timeout_ms)
        page.wait_for_timeout(2000)
        log("navigated", url=page.url)

        # turnstile detection
        sitekey = None
        try:
            sitekey = page.evaluate("""() => {
                const el = document.querySelector('[data-sitekey]');
                if (el) return el.getAttribute('data-sitekey');
                const ifr = [...document.querySelectorAll('iframe')].find(f => (f.src||'').includes('challenges.cloudflare.com'));
                if (ifr) { const m = (ifr.src||'').match(/\\/(0x4[A-Za-z0-9_-]+)\\//); if (m) return m[1]; }
                return null;
            }""")
        except Exception:
            sitekey = None
        has_ts = bool(page.query_selector(".cf-turnstile, [data-sitekey], iframe[src*='challenges.cloudflare.com']"))
        log("turnstile_detect", present=has_ts, sitekey=sitekey)

        # field discovery: gather labeled inputs
        fields = []
        try:
            fields = page.evaluate("""() => {
                const out = [];
                const nodes = document.querySelectorAll('input, textarea, select');
                nodes.forEach((n, i) => {
                    if (n.type === 'hidden' || n.type === 'submit' || n.type === 'button') return;
                    let label = '';
                    if (n.id) {
                        const l = document.querySelector('label[for=\"' + n.id + '\"]');
                        if (l) label = l.innerText.trim();
                    }
                    if (!label) {
                        const wrap = n.closest('label');
                        if (wrap) label = wrap.innerText.trim();
                    }
                    if (!label) label = n.getAttribute('aria-label') || n.getAttribute('placeholder') || n.name || '';
                    out.push({label: label, name: n.name || n.id || ('f'+i), id: n.id || '', type: n.type || n.tagName.toLowerCase(), placeholder: n.getAttribute('placeholder') || ''});
                });
                return out;
            }""")
        except Exception:
            fields = []
        log("fields_discovered", count=len(fields))

        answers = _answer_bank(answers_path)
        profile_facts = profile if isinstance(profile, dict) else {}
        # attach years_experience if present verbatim in profile/cv text
        # (we never invent; leave None unless caller supplies facts.years_experience)
        mapping = map_fields(fields, profile_facts, answers)
        reasons = mapping.pop("_reasons", {}) if isinstance(mapping, dict) else {}
        result["filled"] = mapping
        for k, r in reasons.items():
            result["needs_human"].append({"field": k, "label": "", "reason": r})

        # turnstile solve (cap 3)
        token = None
        if has_ts and sitekey:
            attempts = 0
            while attempts < 3:
                attempts += 1
                result["turnstile_attempts"] = attempts
                try:
                    import sys
                    sys.path.insert(0, str(Path.home() / ".job-autopilot" / "vendor" / "turnstile_solver"))
                    from ts_engine import TurnstileEngine
                    eng = TurnstileEngine(headless=True)
                    r = eng.solve(url=apply_url, sitekey=sitekey, timeout=45.0, keep_open=0)
                    eng.close()
                    if r.get("ok"):
                        token = r.get("token")
                        result["turnstile"] = {"ok": True, "len": r.get("len"), "attempts": attempts}
                        break
                    else:
                        result["turnstile"] = {"ok": False, "error": r.get("error"), "attempts": attempts}
                except Exception as e:
                    result["turnstile"] = {"ok": False, "error": str(e), "attempts": attempts}
                if attempts >= 3:
                    result["needs_human_captcha"] = True
            # inject token
            if token:
                try:
                    page.evaluate("""(t) => {
                        const el = document.querySelector('input[name=\"cf-turnstile-response\"], textarea[name=\"cf-turnstile-response\"]');
                        if (el) el.value = t;
                        if (window.turnstile && window.turnstile.getResponse) {}
                    }""", token)
                    log("turnstile_injected", len=len(token))
                except Exception:
                    pass

        # fill fields
        for i, f in enumerate(fields):
            key = f.get("name") or f.get("id") or f"field_{i}"
            val = mapping.get(key, NEEDS_HUMAN)
            if val == NEEDS_HUMAN:
                result["needs_human"].append({"field": key, "label": f.get("label", "")})
                continue
            try:
                sel = None
                if f.get("id"):
                    sel = f"#{f['id']}"
                elif f.get("name"):
                    sel = f"[name='{f['name']}']"
                if sel:
                    page.fill(sel, str(val))
                    result["actions"].append({"action": "fill", "selector": sel, "value": str(val)[:40]})
            except Exception as e:
                result["needs_human"].append({"field": key, "label": f.get("label", ""), "error": str(e)})

        # CV upload
        file_input = page.query_selector("input[type='file']")
        if file_input is not None and cv is not None:
            try:
                file_input.set_input_files(str(cv))
                result["actions"].append({"action": "upload", "path": str(cv)})
            except Exception as e:
                result["needs_human"].append({"field": "cv_upload", "error": str(e)})

        # screenshot
        shot = adir / "form.png"
        try:
            page.screenshot(path=str(shot), full_page=True)
            result["artifacts"]["screenshot"] = str(shot)
        except Exception:
            pass

        # submit guard (two-key)
        submit_sel = "button[type='submit'], input[type='submit'], button:has-text('Apply'), button:has-text('Submit')"
        # find a real submit button
        try:
            btn = page.query_selector("button[type='submit'], input[type='submit']")
            if btn is None:
                btn = page.query_selector("button")
            if btn is not None:
                result["submit_selector"] = "button[type='submit']"
        except Exception:
            pass
        result["ready_to_submit"] = False
        if allow_submit and confirm_submit:
            # deliberate: still do NOT auto-click unless both keys true AND explicit
            result["ready_to_submit"] = True
            result["actions"].append({"action": "submit_click", "selector": result["submit_selector"]})
            try:
                page.click(result["submit_selector"])
                result["submitted"] = True
            except Exception as e:
                result["needs_human"].append({"field": "submit", "error": str(e)})
        else:
            # record that we deliberately did NOT click
            result["actions"].append({"action": "submit_blocked", "reason": "allow_submit=%s confirm_submit=%s" % (allow_submit, confirm_submit)})
            result["ready_to_submit"] = bool(allow_submit)

        log("done", needs_human=len(result["needs_human"]))
    finally:
        B.close(pw, ctx)

    _finalize(result, adir, steps)
    # handoff.md
    _write_handoff(result, adir)
    return result

def _finalize(result, adir, steps):
    result["artifacts"]["filled_json"] = str(adir / "filled.json")
    result["artifacts"]["steps_jsonl"] = str(adir / "steps.jsonl")
    result["artifacts"]["handoff_md"] = str(adir / "handoff.md")
    _write_json(adir / "filled.json", result)
    (adir / "steps.jsonl").write_text("".join(json.dumps(s) + "\n" for s in steps))

def _write_handoff(result, adir):
    lines = []
    lines.append("# Form handoff — %s / %s" % (result.get("company"), result.get("role")))
    lines.append("")
    lines.append("ATS detected: %s" % result.get("ats"))
    lines.append("Apply URL: %s" % result.get("apply_url"))
    lines.append("CV used: %s" % result.get("cv_used"))
    lines.append("")
    lines.append("## Filled fields")
    for k, v in (result.get("filled") or {}).items():
        lines.append("- %s: %s" % (k, v))
    lines.append("")
    lines.append("## Needs human")
    for n in result.get("needs_human") or []:
        lines.append("- %s" % n)
    lines.append("")
    if result.get("needs_human_captcha"):
        lines.append("Captcha: 3 attempts exhausted — solve manually.")
    lines.append("")
    lines.append("## Next actions for the human")
    lines.append("1. Open the screenshot: %s" % result["artifacts"].get("screenshot"))
    lines.append("2. Review filled fields; correct any NEEDS_HUMAN.")
    lines.append("3. Click submit manually (auto-submit is %s)." % ("ON" if result.get("ready_to_submit") else "OFF"))
    (adir / "handoff.md").write_text("\n".join(lines) + "\n")