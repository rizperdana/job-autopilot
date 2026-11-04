"""Configuration loading for job-autopilot (stdlib only)."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _expand(p):
    return Path(os.path.expanduser(os.path.expandvars(str(p))))


def _parse_yaml(text):
    # minimal tolerant line-based YAML parser (mappings + sequences + scalars)
    lines = []
    for raw in text.splitlines():
        s = raw.rstrip()
        if not s.strip() or s.lstrip().startswith("#"):
            continue
        # strip inline comments (naive: not inside quotes)
        code = s
        # remove trailing comment
        inq = False
        out = []
        i = 0
        while i < len(code):
            c = code[i]
            if c == '"':
                inq = not inq
                out.append(c)
            elif c == "#" and not inq and (i == 0 or code[i - 1].isspace()):
                break
            else:
                out.append(c)
            i += 1
        code = "".join(out).rstrip()
        if not code.strip():
            continue
        indent = len(code) - len(code.lstrip(" "))
        lines.append((indent, code.strip()))
    if not lines:
        return {}

    def parse_scalar(v):
        v = v.strip()
        if v == "" or v.lower() in ("null", "~", "none"):
            return None
        if v.lower() in ("true", "yes"):
            return True
        if v.lower() in ("false", "no"):
            return False
        if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
            return v[1:-1]
        if v.startswith("[") and v.endswith("]"):
            inner = v[1:-1].strip()
            if not inner:
                return []
            return [parse_scalar(x.strip()) for x in inner.split(",")]
        try:
            if "." in v:
                return float(v)
            return int(v)
        except ValueError:
            return v

    def parse_block(idx, indent):
        if idx >= len(lines):
            return {}, idx
        if lines[idx][0] < indent:
            return None, idx
        # sequence?
        if lines[idx][1].startswith("- "):
            seq = []
            while idx < len(lines) and lines[idx][0] == indent and lines[idx][1].startswith("- "):
                item = lines[idx][1][2:].strip()
                idx += 1
                if item == "":
                    val, idx = parse_block(idx, indent + 2)
                    seq.append(val)
                elif ":" in item:
                    key, _, rest = item.partition(":")
                    key = key.strip()
                    rest = rest.strip()
                    d = {}
                    if rest:
                        d[key] = parse_scalar(rest)
                    else:
                        if idx < len(lines) and lines[idx][0] > indent:
                            v, idx = parse_block(idx, lines[idx][0])
                            d[key] = v
                        else:
                            d[key] = None
                    # additional keys at indent+2
                    while idx < len(lines) and lines[idx][0] == indent + 2 and not lines[idx][1].startswith("- "):
                        k, _, r = lines[idx][1].partition(":")
                        k = k.strip()
                        r = r.strip()
                        idx += 1
                        if r:
                            d[k] = parse_scalar(r)
                        else:
                            if idx < len(lines) and lines[idx][0] > indent + 2:
                                v, idx = parse_block(idx, lines[idx][0])
                                d[k] = v
                            else:
                                d[k] = None
                    seq.append(d)
                else:
                    seq.append(parse_scalar(item))
            return seq, idx
        # mapping
        d = {}
        while idx < len(lines) and lines[idx][0] == indent:
            key, sep, rest = lines[idx][1].partition(":")
            if not sep:
                idx += 1
                continue
            key = key.strip()
            rest = rest.strip()
            idx += 1
            if rest:
                d[key] = parse_scalar(rest)
            else:
                if idx < len(lines) and lines[idx][0] > indent:
                    v, idx = parse_block(idx, lines[idx][0])
                    d[key] = v
                else:
                    d[key] = None
        return d, idx

    result, _ = parse_block(0, lines[0][0])
    return result if result is not None else {}


def load_config():
    cfg_path = ROOT / "config" / "ja.yml"
    if not cfg_path.exists():
        cfg_path = ROOT / "config" / "ja.example.yml"
    if cfg_path.exists():
        return _parse_yaml(cfg_path.read_text())
    return {}


_CONFIG = None


def config():
    global _CONFIG
    if _CONFIG is None:
        _CONFIG = load_config()
    return _CONFIG


def get(path, default=None):
    cur = config()
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return default
    return cur


def career_ops_dir():
    return _expand(get("paths.career_ops", "~/career-ops"))


def runtime_dir():
    return _expand(get("paths.runtime", "~/.job-autopilot"))


def dataset_path():
    return Path(_expand(get("paths.dataset", "~/.job-autopilot/datasets")))


def profile_path():
    return career_ops_dir() / "config" / "profile.yml"


def cv_path():
    return career_ops_dir() / "cv.md"


def profile():
    p = profile_path()
    if not p.exists():
        return {}
    return _parse_yaml(p.read_text())


def cv_text():
    p = cv_path()
    if not p.exists():
        return ""
    return p.read_text()


def candidate_skills():
    text = cv_text().lower()
    # pull skills section roughly
    skills = set()
    for line in cv_text().splitlines():
        if line.strip().startswith("- **") and "**:" in line:
            # parse "Languages: Python, Ruby, ..."
            after = line.split("**:", 1)[-1]
            for tok in after.replace(",", " ").split():
                tok = tok.strip().strip("*").lower()
                if tok:
                    skills.add(tok)
    # also add target roles words
    for r in (profile().get("target_roles", {}).get("primary", []) or []):
        for tok in r.lower().split():
            skills.add(tok)
    # fallback common
    for w in ["python", "ruby", "javascript", "php", "sql", "react", "docker", "aws", "celery", "postgresql", "mysql", "elasticsearch", "rest", "api", "llm", "rag"]:
        if w in text:
            skills.add(w)
    return skills


def target_roles():
    p = profile()
    tr = p.get("target_roles", {}) or {}
    primary = tr.get("primary", []) or []
    arch = [a.get("name") for a in (tr.get("archetypes", []) or []) if isinstance(a, dict)]
    return list(primary) + [a for a in arch if a not in primary]


def min_salary_usd():
    return 40000


def max_drafts_per_day():
    return int(get("policy.max_drafts_per_day", 10))


def allow_automated_submit():
    return bool(get("policy.allow_automated_submit", False))


def apply_threshold():
    return int(get("gates.apply_threshold", 60))


def review_threshold():
    return int(get("gates.review_threshold", 45))
