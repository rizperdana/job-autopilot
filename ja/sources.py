"""Sources: normalize remoteintech dataset into jobs.jsonl."""
import hashlib
import json
import os

from . import config


import time
import urllib.request


def _norm_role(role):
    return " ".join((role or "").lower().split())


# ISO 3166-1 alpha-2 -> the spelling ja.gates already uses for that country. Codes we
# cannot place stay verbatim, so the gate reports them as unclassified instead of guessing.
_COUNTRY_CODES = {
    "us": "united states", "gb": "united kingdom", "uk": "united kingdom",
    "de": "germany", "fr": "france", "jp": "japan", "au": "australia",
    "ca": "canada", "sg": "singapore", "nl": "netherlands", "ch": "switzerland",
    "ie": "ireland", "se": "sweden", "dk": "denmark", "no": "norway",
    "fi": "finland", "nz": "new zealand", "at": "austria", "be": "belgium",
    "es": "spain", "it": "italy", "pt": "portugal", "kr": "south korea",
    "il": "israel", "ae": "united arab emirates", "lu": "luxembourg",
    "is": "iceland", "pl": "poland", "cz": "czechia", "si": "slovenia",
    "ee": "estonia", "lt": "lithuania", "lv": "latvia", "sk": "slovakia",
    "gr": "greece", "cy": "cyprus", "mt": "malta", "qa": "qatar",
    "sa": "saudi arabia", "kw": "kuwait", "bh": "bahrain", "om": "oman",
    "id": "indonesia", "ph": "philippines", "vn": "vietnam", "th": "thailand",
    "my": "malaysia", "in": "india", "pk": "pakistan", "bd": "bangladesh",
    "lk": "sri lanka", "np": "nepal", "kh": "cambodia", "la": "laos",
    "mm": "myanmar", "ng": "nigeria", "ke": "kenya", "gh": "ghana",
    "eg": "egypt", "ma": "morocco", "tn": "tunisia", "za": "south africa",
    "br": "brazil", "ar": "argentina", "co": "colombia", "pe": "peru",
    "cl": "chile", "mx": "mexico", "bo": "bolivia", "ec": "ecuador",
    "ve": "venezuela", "ua": "ukraine", "ro": "romania", "bg": "bulgaria",
    "rs": "serbia", "hr": "croatia", "hu": "hungary", "ru": "russia",
    "by": "belarus", "tr": "turkey", "cn": "china", "mv": "maldives",
    "tw": "taiwan", "hk": "hong kong", "mo": "macau", "bn": "brunei",
    "tl": "timor-leste", "pg": "papua new guinea", "fj": "fiji",
    "mn": "mongolia", "kz": "kazakhstan",
}

_COUNTRY_ALIASES = {
    "u s": "united states", "u s a": "united states", "usa": "united states",
    "us": "united states", "united states of america": "united states",
    "uk": "united kingdom", "england": "united kingdom", "scotland": "united kingdom",
    "wales": "united kingdom", "great britain": "united kingdom", "britain": "united kingdom",
    "northern ireland": "united kingdom", "holland": "netherlands",
    "the netherlands": "netherlands", "nederland": "netherlands",
    "deutschland": "germany", "polska": "poland", "brasil": "brazil",
    "espana": "spain", "españa": "spain", "italia": "italy",
    "osterreich": "austria", "österreich": "austria", "schweiz": "switzerland",
    "suisse": "switzerland", "turkiye": "turkey", "türkiye": "turkey",
    "uae": "united arab emirates", "emirates": "united arab emirates",
    "czech republic": "czechia", "republic of korea": "south korea",
    "korea": "south korea", "korea republic of": "south korea",
    "russian federation": "russia", "viet nam": "vietnam",
    "hong kong sar": "hong kong", "china mainland": "china",
    "mainland china": "china", "taiwan region": "taiwan",
}

# ATS country fields sometimes hold a scope word instead of a country ("Remote",
# "Worldwide"). Those are not a country scope, so they must not become one.
_NON_COUNTRY = {"remote", "remotely", "anywhere", "worldwide", "worldwide remote",
                "global", "globally", "home", "home based", "flexible", "various",
                "multiple", "any", "none", "unknown", "n/a", "-", "telecommute",
                "distributed", "virtual", "world"}


def _norm_country(value):
    """"United States" / "US" / "Brasil" -> one spelling. Unmapped values pass through."""
    v = " ".join(str(value or "").lower().replace(".", " ").replace(",", " ").split())
    if not v or v in _NON_COUNTRY:
        return None
    if v in _COUNTRY_CODES and len(v) == 2:
        return _COUNTRY_CODES[v]
    if v in _COUNTRY_ALIASES:
        return _COUNTRY_ALIASES[v]
    return v


def _norm_countries(values):
    out = []
    for raw in values or []:
        c = _norm_country(raw)
        if c and c not in out:
            out.append(c)
    return out

def _make_job(company, role, url, apply_url, ats, channel, location_text,
              worldwide_ok, posted_at, source, raw_extra=None, remote=None,
              countries=None):
    """One job record. `remote`/`location_countries` carry what the ATS said outright;
    None/[] means the ATS did not say and the gate falls back to string inference."""
    remote = remote if isinstance(remote, bool) else None
    countries = _norm_countries(countries)
    raw = {
        "company": company, "role": role, "url": url, "apply_url": apply_url,
        "ats": ats, "channel": channel, "location_text": location_text,
        "worldwide_ok": worldwide_ok, "posted_at": posted_at, "source": source,
        "remote": remote, "location_countries": countries,
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
        "remote": remote,
        "location_countries": countries,
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
            job = _make_job(
                name, title, url, apply_url, ats, channel, loc_text,
                worldwide_ok, po.get("updated_at") or po.get("posted_at"),
                "remoteintech", raw_extra, remote=po.get("remote"),
                countries=po.get("countries"))
            job["email"] = email_addresses[0] if email_addresses else None
            jobs.append(job)
    return jobs, dropped
    return jobs

def _from_jobgether(pages=2, keyword="software engineer"):
    """Jobgether public agent API (documented at /astroapi/ai/jobs/docs).
    Returns remote-worldwide software jobs. Failure = empty list, caller decides."""
    jobs = []
    for page in range(1, pages + 1):
        url = ("https://jobgether.com/astroapi/ai/jobs.json"
               "?keyword=%s&locations=anywhere&limit=25&page=%d"
               % (urllib.request.quote(keyword), page))
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "job-autopilot/1.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception as e:  # network hiccup: skip page, keep the rest
            print("jobgether page %d failed: %s" % (page, e))
            break
        rows = data.get("jobs") or []
        if not rows:
            break
        for row in rows:
            title = (row.get("title") or "").strip()
            company = (row.get("company") or "").strip()
            offer = (row.get("url") or "").strip()
            if not (title and company and offer):
                continue
            if "jobgether.com" not in offer:
                continue
            loc = (row.get("location") or "").strip() or "Anywhere"
            jobs.append(_make_job(
                company, title, offer, offer, "jobgether", "form", loc,
                True, (row.get("postedAt") or "").strip() or None, "jobgether"))
        if len(rows) < 25:
            break
        time.sleep(2)  # ponytail: fixed 2s delay, adaptive backoff if Jobgether ever rate-limits us
    return jobs


def run():
    base = config.dataset_path()
    ds_path = base / "dataset.jsonl"
    liv_path = base / "liveness.jsonl"
    pass2_path = base / "pass2.jsonl"
    postings_path = base / "postings.jsonl"


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
    jg = _from_jobgether(pages=2)
    if jg:
        print("jobgether: +%d remote-worldwide software postings" % len(jg))
        jobs = jobs + jg
    else:
        print("jobgether: 0 (unreachable or empty; remoteintech dataset still used)")
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
