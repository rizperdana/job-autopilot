"""Rule-based scam/spam screen. Patterns are data-driven via config/rules/scam_patterns.yml."""
import os
import re
from pathlib import Path

from . import config


def _load_patterns():
    p = config.ROOT / "config" / "rules" / "scam_patterns.yml"
    if not p.exists():
        return []
    try:
        data = config._parse_yaml(p.read_text())
    except Exception:
        return []
    # support list of {id, pattern, ...} or dict
    pats = data.get("patterns", []) if isinstance(data, dict) else []
    out = []
    for item in pats:
        if isinstance(item, dict):
            pid = item.get("id")
            pat = item.get("pattern") or item.get("regex")
            if isinstance(pat, str):
                pat = pat.replace("\\\\", "\\")
            if pid and pat:
                out.append((pid, re.compile(pat, re.IGNORECASE)))
    return out


PATTERNS = _load_patterns()


def scan(text):
    """Return list of {pattern_id, evidence} hits."""
    text = text or ""
    hits = []
    for pid, rx in PATTERNS:
        m = rx.search(text)
        if m:
            start = max(0, m.start() - 40)
            end = min(len(text), m.end() + 40)
            hits.append({"pattern_id": pid, "evidence": text[start:end].strip()})
    return hits


def is_scam(text):
    return len(scan(text)) > 0
