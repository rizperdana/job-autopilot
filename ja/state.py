"""State + report."""
import json
from datetime import date
from pathlib import Path

from . import config


def state_path():
    return config.runtime_dir() / "state.json"


def load_state():
    p = state_path()
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception:
            return {}
    return {}


def save_state(phase, current_task="", blockers=None):
    config.runtime_dir().mkdir(parents=True, exist_ok=True)
    state = {
        "phase": phase,
        "current_task": current_task,
        "blockers": blockers or [],
        "last_updated": date.today().isoformat(),
    }
    state_path().write_text(json.dumps(state, indent=2))


def report():
    from . import tracker
    st = tracker.stats()
    lines = []
    lines.append("# Job Autopilot Report — " + date.today().isoformat())
    lines.append("")
    lines.append("## Funnel")
    for r in st.get("gate_decisions", []):
        lines.append("- {}: {}".format(r.get("gate_decision"), r.get("c")))
    lines.append("")
    # per-gate breakdown G1..G7 (hard rejects)
    rej = {r.get("gate"): r.get("c") for r in st.get("rejection_hist", [])}
    lines.append("## Hard-gate rejections (reject_hard) per gate:")
    for g in ("G1", "G2", "G3", "G4", "G5", "G6", "G7"):
        lines.append("  - {}: {}".format(g, rej.get(g, 0)))
    lines.append("")
    spam = rej.get("G7", 0) + rej.get("scam", 0)
    lines.append("## Spam-flagged count (G7/scam only): {}".format(spam))
    lines.append("")
    for r in st.get("applications_by_status", []):
        lines.append("- {}: {}".format(r.get("status"), r.get("c")))
    lines.append("")
    lines.append("Drafts: {}  Submitted: {}".format(st.get("drafts", 0), st.get("submitted", 0)))
    lines.append("")
    # shortlist top-N
    rt = config.runtime_dir()
    sl = rt / "shortlist.json"
    if sl.exists():
        try:
            data = json.loads(sl.read_text())
            lines.append("## Top shortlisted")
            for j in (data.get("shortlist") or [])[:20]:
                lines.append("- {} | {} | score {} | {}".format(
                    j.get("company"), j.get("role"), j.get("score"), j.get("decision")))
        except Exception:
            pass
    out_dir = config.runtime_dir() / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / (date.today().isoformat() + ".md")
    out.write_text("\n".join(lines) + "\n")
    print("report ->", out)
    return out
