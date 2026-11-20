"""Role priority for job-autopilot: ordering only, never exclusion."""
import re

_ENG = list(dict.fromkeys([
    "machine learning", "full stack", "full-stack", "fullstack",
    "back-end", "backend", "front-end", "frontend", "software",
    "web", "mobile", "api", "data", "ml", "ai", "llm",
    "engineer", "engineering", "developer", "programmer",
]))

_DEVOPS = list(dict.fromkeys([
    "site reliability", "infrastructure", "reliability", "platform",
    "devops", "sre", "infra", "cloud", "systems",
]))

_SENIORITY = list(dict.fromkeys([
    "principal", "director", "manager", "architect", "senior", "lead",
    "staff", "head", "chief", "vp", "sr",
]))


def _pat(words):
    ordered = sorted(set(words), key=len, reverse=True)
    return re.compile(
        r"\b(?:" + "|".join(re.escape(w) for w in ordered) + r")\b",
        re.IGNORECASE,
    )


_ENG_RE = _pat(_ENG)
_DEVOPS_RE = _pat(_DEVOPS)
_SEN_RE = _pat(_SENIORITY)


def role_rank(job):
    """0 = plain SWE IC, 1 = devops family, 2 = senior/lead engineer, 3 = other."""
    title = job.get("role") if isinstance(job, dict) else job
    title = title if isinstance(title, str) else (str(title) if title is not None else "")
    is_devops = bool(_DEVOPS_RE.search(title))
    is_eng = bool(_ENG_RE.search(title))
    is_senior = bool(_SEN_RE.search(title))
    if is_devops:
        return 1
    if is_eng and is_senior:
        return 2
    if is_eng:
        return 0
    return 3


def pick_per_company(jobs):
    """One company = one application. Returns {company_key: best job} by lowest rank."""
    best = {}
    for j in jobs:
        key = (j.get("company") if isinstance(j, dict) else None) or ""
        key = str(key).strip().lower()
        if key not in best or role_rank(j) < role_rank(best[key]):
            best[key] = j
    return best
