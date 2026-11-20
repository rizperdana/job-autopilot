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

# The label ladder, shared by both discovery passes. A human finds a question's
# text by trying these in order; so do we, and we say which one won so the
# confidence row can report "placeholder only" instead of pretending a
# placeholder is a question. Expects the `ladder` helper and returns
# [text, source] with source in for/label-wrap/aria-labelledby/aria-label/
# sibling/legend/heading/placeholder/none.
_LADDER_JS = r"""
const _blockLabel = (n) => {
    const sel = 'h1,h2,h3,h4,h5,h6,[role="heading"],legend,.label,.field-label,'
              + '[class*="field-label"],[class*="question"],[class*="form-label"]';
    let anc = n.parentElement, deep = 0;
    while (anc && deep < 4) {
        for (const c of anc.querySelectorAll(sel)) {
            if (c.contains(n)) continue;
            const t = (c.innerText || '').split('\n')[0].trim();
            if (t && t.length <= 120) return t;
        }
        anc = anc.parentElement; deep++;
    }
    return '';
};
const ladder = (n) => {
    if (n.id) {
        const esc = (s) => String(s).replace(/\\/g, '\\\\').replace(/"/g, '\\"');
        let l = null;
        try { l = document.querySelector('label[for="' + esc(n.id) + '"]'); } catch (e) { l = null; }
        if (l && l.innerText.trim()) return [l.innerText.trim(), 'for'];
    }
    const wrap = n.closest('label');
    if (wrap && wrap.innerText.trim()) return [wrap.innerText.trim(), 'label-wrap'];
    const lb = n.getAttribute('aria-labelledby');
    if (lb) {
        const t = lb.split(/\s+/).map(id => { const e = document.getElementById(id); return e ? (e.innerText || '') : ''; })
            .join(' ').replace(/\s+/g, ' ').trim();
        if (t) return [t, 'aria-labelledby'];
    }
    const al = (n.getAttribute('aria-label') || '').trim();
    if (al) return [al, 'aria-label'];
    let sib = n.previousElementSibling, hops = 0;
    while (sib && hops < 3) {
        const t = (sib.innerText || '').split('\n')[0].trim();
        if (t && t.length <= 120) return [t, 'sibling'];
        sib = sib.previousElementSibling; hops++;
    }
    const fs = n.closest('fieldset');
    if (fs) {
        const lg = fs.querySelector('legend');
        if (lg && lg.innerText.trim()) return [lg.innerText.trim(), 'legend'];
    }
    const bl = _blockLabel(n);
    if (bl) return [bl, 'heading'];
    const ph = (n.getAttribute('placeholder') || '').trim();
    if (ph) return [ph, 'placeholder'];
    return ['', 'none'];
};
"""

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

def _duplicate(company, role, channel="form", exclude_application_id=None):
    """True only if this company+role was already applied to on the SAME channel.

    A row that merely exists in `applications` is NOT a duplicate: discover/gate
    create rows for every posting (status discovered/gated/shortlisted). Only
    drafted/prefilled/submitted mean we already went out the door — and even then
    only on the same channel (a company may invite a web apply after an email).
    exclude_application_id lets a resubmit of the SAME application row through
    (its own prefilled row must not block itself).
    """
    try:
        import sqlite3
        db = tracker.db_path()
        if not db.exists():
            return False
        conn = sqlite3.connect(str(db))
        try:
            sql = ("SELECT j.job_id FROM jobs j JOIN applications a ON a.job_id=j.job_id "
                   "WHERE lower(j.company)=? AND lower(j.role)=? AND lower(a.channel)=? "
                   "AND a.status IN ('drafted','prefilled','submitted') "
                   "AND a.application_id != ? LIMIT 1")
            row = conn.execute(sql, (company.strip().lower(), role.strip().lower(),
                                     channel.strip().lower(), exclude_application_id if exclude_application_id is not None else -1)).fetchone()
        finally:
            conn.close()
        return row is not None
    except Exception:
        return False

def _profile_facts(profile):
    # tolerate a non-dict profile (e.g. caller passed config.profile() un-called):
    # facts become None -> map_fields yields NEEDS_HUMAN instead of crashing
    if not isinstance(profile, dict):
        profile = {}
    c = profile.get("candidate") or {}
    comp = profile.get("compensation") or {}
    loc = profile.get("location") or {}
    cv = config.cv_text() or ""
    # verbatim facts only: read them out of cv.md, never invent
    years = None
    m = re.search(r"with\s+(\d{1,2})\s+years", cv, re.IGNORECASE)
    if m:
        years = m.group(1)
    current_company = None
    m = re.search(r"###\s+([^\n\-]+?)\s+--[^\n]*\n+[^\n]*\n(?:[^\n]*\n)*?([^\n]*Present)", cv)
    if m:
        current_company = m.group(1).strip()
    return {
        "full_name": c.get("full_name"),
        "email": c.get("email"),
        "phone": c.get("phone"),
        "location": c.get("location") or (f"{loc.get('city','')}, {loc.get('country','')}".strip(", ")),
        "linkedin": c.get("linkedin"),
        "github": c.get("github"),
        "portfolio": c.get("portfolio_url"),
        "current_company": current_company,
        "years_experience": years,
        "work_authorized": "Indonesia" if (loc.get("country") == "Indonesia") else None,
        "needs_sponsorship": (profile.get("location") or {}).get("needs_sponsorship"),
        "salary": comp.get("target_range") or comp.get("minimum"),
        "how_heard": "job board",
    }

ANSWERS_DEFAULT = "answers.yml"   # under the runtime dir; may legitimately not exist

def default_answers_path():
    return _runtime() / ANSWERS_DEFAULT

def answers_source(path=None):
    """Resolve the answer-bank path: None -> the default file, falsy -> disabled.

    `answers_path=None` (what both call sites pass) means "use the default
    answers.yml"; passing "" or False is the explicit way to run with no bank at
    all. A missing file is not an error: _answer_bank returns {} and the run
    behaves exactly as it did before the bank was wired in.
    """
    if path is None:
        return default_answers_path()
    return Path(path) if path else None

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

def _has_real_value(value):
    """A value we actually filled: not empty, not None, not the NEEDS_HUMAN sentinel."""
    if value is None:
        return False
    v = str(value).strip()
    return bool(v) and v != NEEDS_HUMAN

def _human_label(label, key, source=None):
    """A label a human can act on: never a bare synthetic key like f8.

    `source` is where the ladder found the text. A placeholder is not a
    question, so we say so instead of passing it off as one.
    """
    label = (label or "").strip()
    if label:
        if (source or "").strip().lower() == "placeholder":
            return "%s (placeholder only)" % label
        return label
    m = re.fullmatch(r"f(\d+)", str(key or ""))
    if m:
        return "Unnamed field %s" % m.group(1)
    return str(key or "")

def _confidence(req_flags, mapping, fields, uploaded, channel="form"):
    """Pure: per-field confidence averaged over the fields the page exposes.

    Rubric, one row per attributable field:
      file input that really uploaded           -> 100 (state uploaded)
      real mapped value the shape check accepts -> 100 (state filled)
      real mapped value the shape check rejects ->  50 (state partial)
      required and no real value                ->   0 (state missing)
      optional and empty                        -> excluded, counted in skipped_optional
      required but unattributable (no name/id)  -> excluded, counted in unresolved_required
    pct is the rounded mean of the included scores; None when nothing is scoreable.
    total/filled/missing cover only required fields we can attribute and address, so
    an element the page never names can neither deflate pct nor look actionable.
    Values never enter this dict: it lands in confidence.json, not the answers file.
    """
    mapping = mapping if isinstance(mapping, dict) else {}
    req_flags = [r for r in (req_flags or []) if isinstance(r, dict)]
    fields = [f for f in (fields or []) if isinstance(f, dict)]
    identified = {(f.get("name") or f.get("id") or "") for f in fields}
    identified.discard("")
    total = filled = unresolved = 0
    missing = []
    req_keys = set()
    for rf in req_flags:
        key = rf.get("name") or rf.get("id") or ""
        # no name/id, or a key the discovery pass never saw: we cannot say which
        # field this is, so it must not be scored (never a fabricated 0).
        if rf.get("synthetic") or not key or (identified and key not in identified):
            unresolved += 1
            continue
        req_keys.add(key)
        label = _human_label(rf.get("label"), key, rf.get("label_source"))
        if (rf.get("type") or "").lower() == "file":
            total += 1
            if uploaded:
                filled += 1
            else:
                missing.append(label)
            continue
        total += 1
        if _has_real_value(mapping.get(key)):
            filled += 1
        else:
            missing.append(label)
    method = "required-fields"
    fallback = False
    if total == 0 and fields:
        method = "discovered-fields"
        fallback = True  # the page declared nothing required: measure all it exposes
        for i, f in enumerate(fields):
            key = f.get("name") or f.get("id") or "field_%d" % i
            label = _human_label(f.get("label"), key, f.get("label_source"))
            total += 1
            if _has_real_value(mapping.get(key)):
                filled += 1
            else:
                missing.append(label)

    rows, scores, skipped = [], [], 0
    seen = set()
    scored_flags = [r for r in req_flags if (r.get("name") or r.get("id") or "") in req_keys]
    for i, f in enumerate(fields + scored_flags):
        key = f.get("name") or f.get("id") or "field_%d" % i
        if key in seen:
            continue
        seen.add(key)
        label = _human_label(f.get("label"), key, f.get("label_source"))
        ftype = (f.get("type") or "").lower() or "text"
        required = key in req_keys or fallback
        value = mapping.get(key)
        if ftype == "file" and uploaded:
            score, state = 100, "uploaded"
        elif _has_real_value(value):
            hay = " ".join(str(f.get(k) or "") for k in ("label", "name", "id", "placeholder")).lower()
            ok, _ = _shape_ok(hay, value)
            score, state = (100, "filled") if ok else (50, "partial")
        elif required:
            score, state = 0, "missing"
        else:
            skipped += 1
            continue
        src = (f.get("label_source") or "").strip().lower()
        row = {"label": label, "name": str(key), "type": ftype,
               "required": bool(required), "score": score, "state": state}
        # only the informative sources travel with the row: a page that labels
        # its fields properly says nothing extra
        if src and src not in ("for", "label-wrap"):
            row["label_source"] = src
        rows.append(row)
        scores.append(score)
    return {
        "pct": int(round(sum(scores) / float(len(scores)))) if scores else None,
        "method": method,
        "total": total,
        "filled": filled,
        "missing": missing[:8],
        "channel": channel,
        "fields": rows,
        "skipped_optional": skipped,
        "unresolved_required": unresolved,
        "placeholder_only": sum(1 for r in rows if r.get("label_source") == "placeholder"),
    }

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
                       timeout_ms=30000, attach=None, cv_path=None, cv=None, gate=None):
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
        "password_fields": [],
        "gate": None,
        "actions": [],
        # nothing measured yet: early returns (blacklist/duplicate/dead posting)
        # keep this shape so the key is always present, with pct null.
        "confidence": _confidence([], {}, [], False, "form"),
        "artifacts": {},
    }

    # preflight: blacklist
    if _blacklisted(company):
        result["stopped"] = "blacklist"
        log("stopped", reason="blacklist")
        _finalize(result, adir, steps)
        return result
    # preflight: duplicate (never self-match the row being submitted)
    if _duplicate(company, role, exclude_application_id=job.get("application_id")):
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

        # dead-posting guard: ashby/personio render a "Job not found" page (HTTP 200)
        try:
            body_txt = page.evaluate("document.body.innerText.slice(0, 400)")
            if body_txt and "not found" in body_txt.lower():
                result["stopped"] = "dead"
                log("stopped", reason="job not found (posting offline)")
                _finalize(result, adir, steps)
                return result
        except Exception:
            pass

        # personio hides the form behind an Apply button
        if (ats or "") == "personio":
            try:
                link = page.query_selector("a:has-text('Apply')")
                if link:
                    link.click()
                    page.wait_for_timeout(3000)
                    log("personio_apply_clicked", url=page.url)
            except Exception:
                pass

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
                %s
                const out = [];
                document.querySelectorAll('input, textarea, select').forEach((n, i) => {
                    if (n.type === 'hidden' || n.type === 'submit' || n.type === 'button' || n.type === 'search') return;
                    // no name and no id: nothing can address it again (react-select's
                    // requiredInput mirror, tracking inputs). Not a fillable field.
                    if (!n.name && !n.id) return;
                    const [label, src] = ladder(n);
                    out.push({label: label, label_source: src, name: n.name || n.id || ('f'+i),
                              id: n.id || '', type: n.type || n.tagName.toLowerCase(),
                              placeholder: n.getAttribute('placeholder') || ''});
                });
                return out;
            }""" % _LADDER_JS)
        except Exception:
            fields = []
        log("fields_discovered", count=len(fields))
        # a password input means a login/auth wall, never a job question: the
        # submit gate refuses on it (never type a credential into an ATS form).
        result["password_fields"] = [f.get("name") or f.get("id") for f in fields
                                     if (f.get("type") or "").lower() == "password"]
        if result["password_fields"]:
            log("password_fields", count=len(result["password_fields"]))

        bank_path = answers_source(answers_path)
        answers = _answer_bank(bank_path)
        log("answer_bank", path=str(bank_path) if bank_path else None, keys=len(answers))
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
                # attribute selectors with quoted values: only \\ and " need escaping,
                # immune to Radix ids with '.' and ':'
                if f.get("id"):
                    sel = '[id="%s"]' % str(f["id"]).replace("\\", "\\\\").replace('"', '\\"')
                elif f.get("name"):
                    sel = '[name="%s"]' % str(f["name"]).replace("\\", "\\\\").replace('"', '\\"')
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

        # required-but-unanswered gate: a REQUIRED field left NEEDS_HUMAN blocks
        # submission (never invent). A field is required when its label carries a
        # required marker or the element declares required/aria-required.
        required_missing = []
        try:
            req_flags = page.evaluate("""() => {
                %s
                const out = [];
                document.querySelectorAll('input, textarea, select').forEach((n, i) => {
                    if (n.type === 'hidden' || n.type === 'submit' || n.type === 'button' || n.type === 'search') return;
                    const [label, src] = ladder(n);
                    const required = n.required || n.getAttribute('aria-required') === 'true'
                        || /\\*|required/i.test(label);
                    // synthetic: no name/id, so the score treats it as unattributable
                    // instead of scoring a field nobody can point at. With no
                    // readable question text either it is counted, never scored
                    // and never blocking (see _confidence.unresolved_required).
                    if (required) out.push({name: n.name || n.id || ('f'+i), label: label,
                        label_source: src, type: (n.type || '').toLowerCase(),
                        synthetic: !(n.name || n.id)});
                });
                return out;
            }""" % _LADDER_JS)
        except Exception:
            req_flags = []
        seen_keys = {f.get("name") or f.get("id") or ("f%d" % i) for i, f in enumerate(fields)}
        uploaded = any(isinstance(a, dict) and a.get("action") == "upload" for a in result["actions"])
        for rf in req_flags or []:
            if (rf.get("type") or "") == "file" and uploaded:
                continue
            k = rf.get("name")
            if k in seen_keys and mapping.get(k) == NEEDS_HUMAN:
                required_missing.append(rf.get("label") or k)

        # confidence in what we filled: page's own required fields (upload counts)
        result["confidence"] = _confidence(req_flags, mapping, fields, uploaded, "form")
        log("confidence", **{k: v for k, v in result["confidence"].items()
                             if k not in ("missing", "fields")})

        if required_missing:
            result["submit_blocked_reason"] = "missing required: " + "; ".join(required_missing[:8])
            result["required_missing"] = required_missing

        # screenshot
        shot = adir / "form.png"
        try:
            page.screenshot(path=str(shot), full_page=True)
            result["artifacts"]["screenshot"] = str(shot)
        except Exception:
            pass

        # submit guard (two-key): both keys true AND no required-missing gate
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
        # gate: an optional caller-supplied veto, evaluated where the decision is
        # made (pct is only known here). None = no extra condition, exactly as
        # before. The two keys above stay in force either way.
        gate_ok, gate_reason = (True, "") if gate is None else gate(result)
        result["gate"] = {"allowed": bool(gate_ok), "reason": gate_reason}
        result["ready_to_submit"] = False
        if allow_submit and confirm_submit and gate_ok and not result.get("submit_blocked_reason"):
            # deliberate: still do NOT auto-click unless both keys true AND explicit
            result["ready_to_submit"] = True
            result["actions"].append({"action": "submit_click", "selector": result["submit_selector"]})
            try:
                page.click(result["submit_selector"])
                page.wait_for_timeout(4000)
                # capture confirmation evidence (final URL + body signals)
                try:
                    final_url = page.url
                    body_txt = page.evaluate("document.body.innerText.slice(0, 1500)")
                    m = re.findall(r"(thank|received|success|submitted|application sent|congratulations|we.?ve got|applied)", body_txt or "", re.I)
                    result["confirmation"] = {"url": final_url, "signals": sorted(set(m))[:10]}
                except Exception:
                    pass
                try:
                    page.screenshot(path=str(adir / "submitted.png"), full_page=True)
                    result["artifacts"]["submitted_screenshot"] = str(adir / "submitted.png")
                except Exception:
                    pass
                result["submitted"] = True
            except Exception as e:
                result["needs_human"].append({"field": "submit", "error": str(e)})
        else:
            # record that we deliberately did NOT click
            reason = (result.get("submit_blocked_reason") or gate_reason
                      or ("allow_submit=%s confirm_submit=%s" % (allow_submit, confirm_submit)))
            result["actions"].append({"action": "submit_blocked", "reason": reason})
            result["ready_to_submit"] = bool(allow_submit)

        # collapse per-option noise: radio/checkbox groups repeat one field key per option
        _seen = {}
        for _nh in result["needs_human"]:
            _seen.setdefault(_nh.get("field"), _nh)
        result["needs_human"] = list(_seen.values())
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
    conf = result.get("confidence")
    if isinstance(conf, dict):
        p = Path(adir) / "confidence.json"
        tmp = p.with_name(p.name + ".tmp")
        try:
            tmp.write_text(json.dumps(conf, indent=2, ensure_ascii=False))
            os.replace(tmp, p)
            result["artifacts"]["confidence_json"] = str(p)
        except Exception:
            pass
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
    if result.get("submit_blocked_reason"):
        lines.append("Submit blocked: %s" % result["submit_blocked_reason"])
        lines.append("")
    lines.append("## Next actions for the human")
    lines.append("1. Open the screenshot: %s" % result["artifacts"].get("screenshot"))
    lines.append("2. Review filled fields; correct any NEEDS_HUMAN.")
    lines.append("3. Click submit manually (auto-submit is %s)." % ("ON" if result.get("ready_to_submit") else "OFF"))
    (adir / "handoff.md").write_text("\n".join(lines) + "\n")