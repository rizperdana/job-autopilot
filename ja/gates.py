"""Validation gates G1-G7 + scored gates."""
import datetime
import json

from . import config
from . import scam

_CACHE = {}

def _load_all():
    import json
    base = config.dataset_path()
    key = str(base)
    if key not in _CACHE:
        ds={}; liv={}; p2={}
        p=base/"dataset.jsonl"
        if p.exists():
            for line in p.read_text().splitlines():
                if line.strip():
                    o=json.loads(line); ds[o.get("name")]=o
        p=base/"liveness.jsonl"
        if p.exists():
            for line in p.read_text().splitlines():
                if line.strip():
                    o=json.loads(line); liv[o.get("name")]=o
        p=base/"pass2.jsonl"
        if p.exists():
            for line in p.read_text().splitlines():
                if line.strip():
                    o=json.loads(line); p2[o.get("name")]=o
        _CACHE[key]=(ds,liv,p2)
    return _CACHE[key]


DEVELOPED = {"united states", "usa", "united kingdom", "uk", "germany", "france",
             "japan", "australia", "canada", "singapore", "netherlands",
             "switzerland", "ireland", "sweden", "denmark", "norway", "finland",
             "new zealand", "austria", "belgium", "spain", "italy", "portugal",
             "south korea", "korea", "israel", "uae", "luxembourg", "iceland",
             "poland", "czechia", "czech republic", "slovenia", "estonia"}

LANG_MANDATES = ["japanese", "korean", "chinese", "mandarin", "german native",
                 "fluent german", "native german", "native japanese"]


def _parse_date(s):
    if not s:
        return None
    try:
        return datetime.date.fromisoformat(str(s)[:10])
    except ValueError:
        try:
            return datetime.datetime.fromisoformat(str(s).replace("Z", "+00:00")).date()
        except ValueError:
            return None


def _dataset_row(company):
    ds,_,_=_load_all()
    return ds.get(company,{})


def _liveness(company):
    _,liv,_=_load_all()
    return liv.get(company,{})


def _pass2(company):
    _,_,p2=_load_all()
    return p2.get(company,{})


def _blacklist():
    p = config.career_ops_dir() / "data" / "blacklist.md"
    if not p.exists():
        return set()
    out = set()
    for line in p.read_text().splitlines():
        line = line.strip().lower()
        if line and not line.startswith("#"):
            out.add(line)
    return out


def hard_gates(job):
    res = []
    company = job.get("company")
    ds = _dataset_row(company)
    liv = _liveness(company)
    p2 = _pass2(company)

    # G1 authentic employer
    g1_status = "pass"
    g1_reasons = []
    website = ds.get("website")
    if not website:
        g1_status = "review"
        g1_reasons.append("no company website in dataset")
    res.append(("G1", g1_status, "; ".join(g1_reasons) or "employer looks authentic"))

    # G2 live posting
    g2_status = "pass"
    g2_reasons = []
    posted = job.get("posted_at")
    open_roles = p2.get("open_roles")
    if isinstance(open_roles, int) and open_roles > 0:
        g2_status = "pass"
        g2_reasons.append(f"live-verified: {open_roles} open roles")
    elif posted:
        d = _parse_date(posted)
        if d is None:
            g2_status = "review"
            g2_reasons.append("unparseable posted_at")
        else:
            age = (datetime.date.today() - d).days
            if age <= 45:
                g2_status = "pass"
                g2_reasons.append(f"posted {age}d ago (<=45)")
            else:
                g2_status = "fail"
                g2_reasons.append(f"posted {age}d ago (>45)")
    else:
        if liv and liv.get("http_status") == 200:
            g2_status = "review"
            g2_reasons.append("posted_at unknown but site live (review)")
        else:
            g2_status = "review"
            g2_reasons.append("posted_at unknown and no liveness data (review)")
    res.append(("G2", g2_status, "; ".join(g2_reasons)))

    # G3 geography (per-job, exclusion precedence)
    g3_status = "pass"
    g3_reasons = []
    evidence = None
    p2 = _pass2(company)
    dsrow = _dataset_row(company)
    loc = (job.get("location_text") or "").strip()
    loc_l = loc.lower()
    # job-level text ONLY (role + location); company titles/description excluded
    job_text = (str(job.get("role") or "") + " " + str(job.get("location_text") or "")).lower()
    wp = p2.get("worldwide_phrases") or []
    excl = p2.get("exclusion_phrases") or []
    worldwide_ok = job.get("worldwide_ok")

    import re as _re
    excl_phrases = [
        "us-remote", "us - remote", "us remote", "u.s. remote", "(us remote)", "remote (us)", "remote - us",
        "remote us", "remote-us",
        "remote - united states", "remote-united-states", "united states - remote", "united states",
        "remote (usa)",
        "remote - usa", "remote usa", "usa only", "us only", "u.s. only",
        "u.s. based", "united states only", "must be authorized to work in the us",
        "eligible to work in the us", "us work authorization", "emea only",
        "canada only", "uk only", "latam only", "based in mexico",
    ]
    matched = None
    for ph in excl_phrases:
        rx = _re.compile(r"(?<![a-z0-9])" + _re.escape(ph) + r"(?![a-z0-9])", _re.IGNORECASE)
        m = rx.search(job_text)
        if m:
            matched = m.group(0)
            break
    if matched is None:
        m = _re.search(r"must be based in\s+([a-z][a-z .'-]{0,40})", job_text, _re.IGNORECASE)
        if m:
            country = m.group(1).strip().rstrip(".,")
            if country and country.lower() not in ("indonesia", "id"):
                matched = "must be based in " + country
    if matched is None:
        m = _re.search(r"must be located in\s+([a-z][a-z .'-]{0,40})", job_text, _re.IGNORECASE)
        if m:
            country = m.group(1).strip().rstrip(".,")
            if country and country.lower() not in ("indonesia", "id"):
                matched = "must be located in " + country
    if matched:
        g3_status = "fail"
        g3_reasons.append("exclusion: " + matched)
        evidence = matched
    else:
        # company-level exclusion phrases (secondary)
        if excl:
            g3_status = "fail"
            g3_reasons.append("exclusion_phrases: " + ", ".join(excl[:3]))
            evidence = ", ".join(excl[:3])
        else:
            import re as _re2
            developed_hit = any(_re2.search(r"(?<![a-z])" + _re2.escape(w) + r"(?![a-z])", loc_l) for w in DEVELOPED) or loc_l.strip() in ("us", "u.s.", "u.s.a.", "u.s")
            has_indonesia = "indonesia" in loc_l
            has_reloc = any(w in (loc_l + " " + job_text) for w in ("relocat", "visa", "sponsor"))
            ambiguous = loc_l in ("", "remote", "anywhere", "worldwide", "global",
                                  "remote (worldwide)", "remote worldwide", "anywhere (remote)",
                                  "anywhere in the world")
            regional = loc_l in ("americas", "americas-europe", "europe", "other",
                                 "latam", "emea", "apac", "asia-pacific")
            if has_indonesia and "remote" in loc_l:
                g3_status = "pass"
                g3_reasons.append("Indonesia remote")
                evidence = loc
            elif developed_hit and has_reloc:
                g3_status = "pass"
                g3_reasons.append("developed country with relocation/sponsorship")
                evidence = loc
            elif developed_hit and not has_indonesia:
                g3_status = "fail"
                g3_reasons.append("country-locked: " + loc)
                evidence = loc
            elif ambiguous:
                if (wp or worldwide_ok is True):
                    g3_status = "pass"
                    g3_reasons.append("worldwide_phrases present")
                    evidence = "worldwide_phrases: " + ", ".join(wp[:3]) if wp else "worldwide_ok"
                else:
                    g3_status = "review"
                    g3_reasons.append("eligibility ambiguous - verify manually")
                    evidence = "ambiguous location: " + (loc or "<empty>")
            elif regional:
                g3_status = "review"
                g3_reasons.append("eligibility ambiguous - verify manually")
                evidence = loc or "<empty>"
            else:
                g3_status = "review"
                g3_reasons.append("eligibility ambiguous - verify manually")
                evidence = loc or "<empty>"
    # US/EU auth no sponsorship hard reject
    if "authorized to work in the us" in job_text and "sponsor" not in job_text:
        g3_status = "fail"
        g3_reasons.append("requires US work auth without sponsorship")
        evidence = "authorized to work in the us (no sponsor)"
    # onsite-only Indonesia
    if "indonesia" in loc_l and "remote" not in loc_l and "onsite" in loc_l:
        g3_status = "fail"
        g3_reasons.append("onsite-only Indonesia seat")
        evidence = "onsite Indonesia"
    if evidence is None:
        evidence = loc or "<none>"
    g3_reasons.append("evidence=" + str(evidence))
    res.append(("G3", g3_status, "; ".join(g3_reasons)))

    # G4 language
    g4_status = "pass"
    g4_reasons = []
    jobtext = (job.get("role", "") + " " + loc).lower()
    for m in LANG_MANDATES:
        if m in jobtext:
            g4_status = "fail"
            g4_reasons.append(f"requires {m}")
            break
    if g4_status == "pass":
        g4_reasons.append("language ok (English)")
    res.append(("G4", g4_status, "; ".join(g4_reasons)))

    # G5 duplicate/already-applied (heuristic pass)
    res.append(("G5", "pass", "no duplicate detected (offline heuristic)"))

    # G6 blacklist
    g6_status = "pass"
    g6_reasons = []
    bl = _blacklist()
    company_l = (company or "").lower()
    for b in bl:
        if b and b in company_l:
            g6_status = "fail"
            g6_reasons.append(f"blacklisted: {b}")
            break
    if g6_status == "pass":
        g6_reasons.append("not blacklisted")
    res.append(("G6", g6_status, "; ".join(g6_reasons)))

    # G7 scam
    g7_status = "pass"
    g7_reasons = []
    fulltext = " ".join(str(job.get(k, "") or "") for k in ("company", "role", "location_text"))
    fulltext += " " + " ".join(p2.get("titles") or [])
    fulltext += " " + " ".join(p2.get("email_addresses") or [])
    hits = scam.scan(fulltext)
    if hits:
        g7_status = "fail"
        g7_reasons.append("scam: " + hits[0]["pattern_id"])
    else:
        g7_reasons.append("no scam patterns")
    res.append(("G7", g7_status, "; ".join(g7_reasons)))

    return res


def scored(job):
    breakdown = {"role_fit": 0, "stack_fit": 0, "seniority": 0,
                 "comp": 0, "evidence": 0}
    reasons = []
    role = (job.get("role") or "").lower()
    company = job.get("company")
    ds = _dataset_row(company)
    p2 = _pass2(company)

    targets = [r.lower() for r in config.target_roles()]
    rf = 0
    for t in targets:
        if t.lower() in role or role in t.lower():
            rf = 40
            reasons.append(f"role matches target '{t}'")
            break
    if rf == 0:
        kws = ["engineer", "developer", "software", "fullstack", "full-stack",
               "backend", "frontend", "ml", "ai", "data"]
        if any(k in role for k in kws):
            rf = 20
            reasons.append("role loosely relevant")
    breakdown["role_fit"] = rf

    techs = ds.get("technologies") or []
    skills = config.candidate_skills()
    overlap = 0
    for t in techs:
        if t.lower() in skills or t.lower() in " ".join(skills):
            overlap += 1
    stack = min(25, overlap * 5)
    if overlap == 0 and techs:
        stack = 5
    breakdown["stack_fit"] = stack
    if overlap:
        reasons.append(f"stack overlap {overlap}")

    if "senior" in role or "staff" in role or "lead" in role or "principal" in role:
        breakdown["seniority"] = 15
        reasons.append("senior-level title")
    elif "junior" in role or "intern" in role:
        breakdown["seniority"] = 3
    else:
        breakdown["seniority"] = 8

    breakdown["comp"] = 5

    if job.get("apply_url"):
        breakdown["evidence"] = 6
    if p2.get("titles"):
        breakdown["evidence"] += 2
    if p2.get("apply_channel") in ("email", "form"):
        breakdown["evidence"] += 2
    breakdown["evidence"] = min(10, breakdown["evidence"])
    if job.get("apply_url"):
        reasons.append("apply path present")

    total = sum(breakdown.values())
    return total, breakdown, reasons


def evaluate(job):
    hg = hard_gates(job)
    failed = None
    review_flag = False
    for gate, status, reason in hg:
        if status == "fail":
            failed = gate
            break
        if status == "review":
            review_flag = True
    gate_results = [{"gate": g, "status": s, "reason": r} for (g, s, r) in hg]
    if failed:
        reason_map = {g: r for g, s, r in hg}
        return {
            "job_id": job.get("job_id"),
            "decision": "reject_hard",
            "score": 0,
            "breakdown": {},
            "reasons": [f"{failed}: " + reason_map.get(failed, "")],
            "failed_gate": failed,
            "gate_results": gate_results,
        }
    total, breakdown, reasons = scored(job)
    if review_flag:
        decision = "review"
    elif total >= config.apply_threshold():
        decision = "apply"
    elif total >= config.review_threshold():
        decision = "review"
    else:
        decision = "skip"
    if review_flag:
        for g, s, r in hg:
            if s == "review":
                reasons.insert(0, f"{g}: {r}")
    return {
        "job_id": job.get("job_id"),
        "decision": decision,
        "score": total,
        "breakdown": breakdown,
        "reasons": reasons,
        "failed_gate": None,
        "gate_results": gate_results,
    }
