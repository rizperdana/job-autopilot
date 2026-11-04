"""Sources: normalize remoteintech dataset into jobs.jsonl."""
import hashlib
import json
import os

from . import config


def _norm_role(role):
    return " ".join((role or "").lower().split())


def _make_job(company, role, url, apply_url, ats, channel, location_text,
              worldwide_ok, posted_at, source, raw_extra=None):
    raw = {
        "company": company, "role": role, "url": url, "apply_url": apply_url,
        "ats": ats, "channel": channel, "location_text": location_text,
        "worldwide_ok": worldwide_ok, "posted_at": posted_at, "source": source,
    }
    if raw_extra:
        raw.update(raw_extra)
    dh = hashlib.sha256(
        ((company or "").strip().lower() + "|" + _norm_role(role) + "|" +
         (apply_url or url or "").strip()).encode()
    ).hexdigest()
    return {
        "job_id": dh[:16],
        "company": company,
        "role": role,
        "url": url,
        "apply_url": apply_url,
        "ats": ats,
        "channel": channel,
        "location_text": location_text,
        "worldwide_ok": bool(worldwide_ok) if worldwide_ok is not None else None,
        "posted_at": posted_at,
        "source": source,
        "raw": raw,
        "dedupe_hash": dh,
    }


def _is_valid_posting(po, careers_url=None):
    src = (po.get("source") or "").lower()
    if src == "api":
        return True
    if src != "html":
        return False
    title = (po.get("title") or "").strip()
    if not title:
        return False
    junk = {"careers", "join us", "our story", "about us", "contact",
            "open positions", "we're hiring", "open roles", "life at",
            "benefits", "culture", "why work with us", "talent community"}
    if title.lower() in junk:
        return False
    url = (po.get("url") or po.get("apply_url") or "").strip().lower()
    if not url:
        return False
    if url.endswith("/feed") or url.endswith("/feed/"):
        return False
    if url.startswith("#") or url.endswith("#"):
        return False
    if url.endswith("/careers") or url.endswith("/careers/"):
        return False
    if careers_url and url.rstrip("/") == careers_url.rstrip("/").lower():
        return False
    return True

def _from_dataset(ds, livemap, pass2map, postings_path=None):
    jobs = []
    dropped = 0
    # build lookup of postings by company name (case-insensitive)
    postings_by_company = {}
    if postings_path is not None and postings_path.exists():
        for line in postings_path.read_text().splitlines():
            if not line.strip():
                continue
            o = json.loads(line)
            postings_by_company.setdefault((o.get("company") or "").lower(), []).append(o)
    for d in ds:
        name = d.get("name")
        liv = livemap.get(name, {})
        p2 = pass2map.get(name, {})
        ats = liv.get("ats") or (p2.get("ats_embed") or "custom")
        if ats == "custom" and p2.get("ats_embed"):
            ats = p2.get("ats_embed")
        careers = d.get("careers_url") or liv.get("careers_url") or p2.get("careers_url")
        worldwide_phrases = p2.get("worldwide_phrases") or []
        excl = p2.get("exclusion_phrases") or []
        raw_emails = p2.get("email_addresses") or []
        allowed_prefixes = ("careers@", "jobs@", "hr@", "talent@", "recruit@", "apply@")
        email_addresses = []
        dropped_emails = []
        for e in raw_emails:
            el = (e or "").lower().strip()
            if any(el.startswith(pref) for pref in allowed_prefixes):
                email_addresses.append(e)
            else:
                dropped_emails.append(e)
        raw_extra = {
            "email_addresses": email_addresses,
            "dropped_emails": dropped_emails,
            "drop_reason": "aggregator/privacy/non-role address" if dropped_emails else None,
            "ats_embed": p2.get("ats_embed"),
            "ats_slug": p2.get("ats_slug"),
            "open_roles": p2.get("open_roles"),
            "worldwide_phrases": worldwide_phrases,
            "exclusion_phrases": excl,
        }
        apply_channel = p2.get("apply_channel") or "unknown"
        if apply_channel == "email":
            channel = "email"
        elif apply_channel == "form":
            channel = "form"
        else:
            channel = "email" if email_addresses else "form"

        worldwide_ok = None
        if worldwide_phrases:
            worldwide_ok = True
        if excl and not worldwide_phrases:
            worldwide_ok = False
        if worldwide_ok is None:
            worldwide_ok = (d.get("region") == "worldwide")

        # Only build jobs from REAL postings. No synthetic "Software Engineer" rows.
        postings = postings_by_company.get((name or "").lower(), [])
        for po in postings:
            if not _is_valid_posting(po, careers):
                dropped += 1
                continue
            title = po.get("title") or po.get("role")
            if not title:
                dropped += 1
                continue
            loc_text = po.get("location") or po.get("location_text") or ""
            url = po.get("url") or po.get("apply_url") or careers
            apply_url = po.get("apply_url") or po.get("url") or careers
            jobs.append(_make_job(
                name, title, url, apply_url, ats, channel, loc_text,
                worldwide_ok, None, "remoteintech", raw_extra))
    return jobs, dropped
    return jobs


def _from_portals(portals):
    jobs = []
    for c in (portals.get("tracked_companies") or []):
        if not isinstance(c, dict):
            continue
        if c.get("enabled") is False:
            continue
        name = c.get("name")
        if not name:
            continue
        careers = c.get("careers_url")
        api = c.get("api")
        apply_url = careers or api
        role = "Software Engineer"
        jobs.append(_make_job(
            name, role, careers, apply_url, "custom", "form",
            "", True, None, "portals"))
    return jobs


def run():
    base = config.dataset_path()
    ds_path = base / "dataset.jsonl"
    liv_path = base / "liveness.jsonl"
    pass2_path = base / "pass2.jsonl"
    postings_path = base / "postings.jsonl"
    portals_path = config.career_ops_dir() / "portals.yml"

    if not ds_path.exists():
        raise RuntimeError("dataset.jsonl not found at " + str(ds_path) + " (dataset dir: " + str(base) + ")")
    if not postings_path.exists():
        raise RuntimeError("postings.jsonl not found at " + str(postings_path) + " (dataset dir: " + str(base) + ")")
    ds = [json.loads(l) for l in ds_path.read_text().splitlines() if l.strip()]
    if not ds:
        raise RuntimeError("dataset.jsonl is empty: " + str(ds_path))
    postings_lines = [l for l in postings_path.read_text().splitlines() if l.strip()]
    if not postings_lines:
        raise RuntimeError("postings.jsonl is empty: " + str(postings_path))
    livemap = {}
    if liv_path.exists():
        for l in liv_path.read_text().splitlines():
            if l.strip():
                o = json.loads(l)
                livemap[o.get("name")] = o
    else:
        print("liveness.jsonl missing; G2 may be review")
    pass2map = {}
    if pass2_path.exists():
        for l in pass2_path.read_text().splitlines():
            if l.strip():
                o = json.loads(l)
                pass2map[o.get("name")] = o
    else:
        print("pass2.jsonl missing; using directory-only records")

    jobs, dropped = _from_dataset(ds, livemap, pass2map, postings_path)
    print("dropped {} junk postings".format(dropped))

    if portals_path.exists():
        try:
            portals = config._parse_yaml(portals_path.read_text())
            jobs += _from_portals(portals)
        except Exception as e:
            print("could not parse portals.yml:", e)
    else:
        print("portals.yml not found; skipping")

    seen = set()
    out = []
    for j in jobs:
        if j["dedupe_hash"] in seen:
            continue
        seen.add(j["dedupe_hash"])
        out.append(j)

    rt = config.runtime_dir()
    rt.mkdir(parents=True, exist_ok=True)
    target = rt / "jobs.jsonl"
    tmp = rt / ".jobs.jsonl.tmp"
    tmp.write_text("".join(json.dumps(j, ensure_ascii=False) + "\n" for j in out))
    os.replace(tmp, target)
    print("discovered", len(out), "jobs ->", target)
    return out


if __name__ == "__main__":
    run()
