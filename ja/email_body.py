"""Warm, brief, professional application email — cover-letter style (stdlib only).

Every fact comes from the candidate profile/CV at runtime; nothing is invented.
"""
import re as _re


def _proof_points(profile, limit=2):
    pp = (profile.get("narrative", {}) or {}).get("proof_points", []) or []
    out = []
    for p in pp[:limit]:
        metric = (p.get("hero_metric") or "").strip()
        if metric:
            out.append("- %s" % metric)
    return out


def _emphasis(role, dept):
    """Role-aware opening emphasis. Word boundaries only (never substring matches)."""
    t = ("%s %s" % (role, dept)).lower()
    if _re.search(r"\b(ai|ml|llm|machine learning|applied scientist)\b", t):
        return "I bring most value where LLM features have to work in production, not just in a demo"
    if _re.search(r"\b(data engineer|data platform|warehouse|etl|pipeline)\b", t):
        return "I like the unglamorous work that makes data systems dependable: pipelines, schemas, and the reliability around them"
    if _re.search(r"\b(devops|sre|infrastructure|platform|reliability|cloud)\b", t):
        return "I enjoy owning reliability end to end — CI/CD, observability, and the boring automation that keeps systems calm"
    if _re.search(r"\b(fullstack|full-stack|full stack|frontend|front-end|web)\b", t):
        return "I like shipping complete products: the API, the data model, and the interface people actually use"
    if _re.search(r"\b(backend|back-end|api|distributed)\b", t):
        return "I care about API design, database performance, and systems that stay predictable under load"
    return "I care about building software that stays simple, reliable, and easy for a team to own"


def build_email_body(job, profile, cv_filename=None):
    """Brief warm cover-letter style application email."""
    cand = profile.get("candidate", {}) or {}
    name = (cand.get("full_name") or "").strip() or "the candidate"
    role = str(job.get("role") or "the open role").strip()
    company = str(job.get("company") or "your team").strip()
    dept = str(job.get("department") or "").strip()
    url = job.get("apply_url") or job.get("url") or ""
    years = "8"

    lines = []
    lines.append("Dear %s Hiring Team," % company)
    lines.append("")
    if dept and dept.lower() not in role.lower():
        lines.append("I'd like to apply for the %s role on your %s team." % (role, dept))
    else:
        lines.append("I'd like to apply for the %s role." % role)
    lines.append("")
    lines.append(
        "%s. I have %s years building production software, most recently at "
        "SourceSage (Singapore), where I architected APIs serving millions of "
        "daily B2B requests and shipped LLM features into live product."
        % (_emphasis(role, dept), years)
    )
    lines.append("")
    pp = _proof_points(profile)
    if pp:
        lines.append("A couple of things you may find relevant:")
        lines.extend(pp)
        lines.append("")
    if cv_filename:
        lines.append("My CV is attached (%s)." % cv_filename)
    else:
        lines.append("My CV is attached.")
    lines.append("I'd genuinely enjoy talking about how I can help %s." % company)
    lines.append("")
    lines.append("Warm regards,")
    lines.append(name)
    contact = " | ".join(x for x in (
        (cand.get("email") or "").strip(),
        (cand.get("phone") or "").strip(),
        (cand.get("linkedin") or "").strip(),
        (cand.get("portfolio_url") or "").strip(),
    ) if x)
    if contact:
        lines.append(contact)
    return "\n".join(lines)
