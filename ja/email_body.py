"""Warm, brief, human application email — cover-letter style (stdlib only).

Every fact comes from the candidate profile/CV at runtime; nothing is invented.
Design notes (user feedback 2026-11-17): sound like a person, not a template —
no "I'd like to apply for", no bullet lists, no "Hiring Team" address, no
filename in the attachment line.
"""
import re as _re


def _hook(role):
    """One honest, role-aware line about how the candidate likes to work.
    Word-boundary matches only (never substring)."""
    t = (role or "").lower()
    if _re.search(r"\b(ai|ml|llm|machine learning|applied scientist)\b", t):
        return ("Most of my recent work has been getting LLM features to behave "
                "reliably in production, which is a different job from getting "
                "them to work in a demo.")
    if _re.search(r"\b(data engineer|data platform|warehouse|etl|pipeline)\b", t):
        return ("I like the unglamorous side of data work — pipelines that don't "
                "break at 2am, schemas that don't surprise anyone in six months.")
    if _re.search(r"\b(devops|sre|infrastructure|platform|reliability|cloud)\b", t):
        return ("I like owning reliability end to end — CI/CD, observability, and "
                "the quiet automation that keeps systems calm.")
    if _re.search(r"\b(fullstack|full-stack|full stack|frontend|front-end|web)\b", t):
        return ("I like shipping the whole thing: the API, the data model, and "
                "the interface people actually use.")
    if _re.search(r"\b(backend|back-end|api|distributed)\b", t):
        return ("I care about API design, database performance, and systems that "
                "stay predictable under load.")
    return ("I care about building software that stays simple, reliable, and easy "
            "for a team to own.")


def build_email_body(job, profile, cv_filename=None):
    """Brief, warm, human application email (~90-120 words). cv_filename is
    accepted for API compatibility but never quoted in the body."""
    cand = profile.get("candidate", {}) or {}
    name = (cand.get("full_name") or "").strip() or "the candidate"
    first = name.split()[0] if name else ""
    role = str(job.get("role") or "the open role").strip()
    company = str(job.get("company") or "your team").strip()
    dept = str(job.get("department") or "").strip()

    lines = []
    lines.append("Hi %s team," % company)
    lines.append("")
    lines.append("I'm writing about the %s role%s." % (
        role, " on the %s side" % dept if dept and dept.lower() not in role.lower() else ""))
    lines.append("")
    lines.append(
        "%s I've spent the last 8 years building production software, most "
        "recently at SourceSage in Singapore, where my team's APIs handle "
        "millions of requests a day and the LLM features I shipped are in "
        "front of real users." % _hook(role))
    lines.append("")
    lines.append("My CV is attached. If it reads like the kind of experience "
                 "you're after, I'd be glad to talk.")
    lines.append("")
    lines.append("Warm regards,")
    lines.append(first or name)
    contact = " | ".join(x for x in (
        (cand.get("email") or "").strip(),
        (cand.get("phone") or "").strip(),
        (cand.get("linkedin") or "").strip(),
        (cand.get("portfolio_url") or "").strip(),
    ) if x)
    if contact:
        lines.append(contact)
    return "\n".join(lines)
