#!/usr/bin/env python3
"""Read-only localhost dashboard for job-autopilot applications."""
import json, os, re, sqlite3, threading, time, http.server, socketserver, urllib.parse
from pathlib import Path

DB = os.environ.get("DB_PATH", str(Path.home() / "career-ops" / "data" / "job-autopilot.db"))
RUNS = Path(os.environ.get("RUNS_DIR", str(Path.home() / ".job-autopilot" / "runs")))
REVISIONS = Path(os.environ.get("REVISIONS_DIR", os.path.expanduser("~/.job-autopilot/revisions")))
PORT = int(os.environ.get("PORT", "8770"))
HOST = "127.0.0.1"
ALLOWED = {"filled.json", "steps.jsonl", "form.png", "handoff.md"}
JOB_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")

# ponytail: single process-global lock + 5-min TTL cache; enough for one user
_FORM_CACHE = {}
_FORM_LOCK = threading.Lock()
_REV_LOCK = threading.Lock()

def conn():
    c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=2)
    c.execute("PRAGMA query_only=ON")
    c.execute("PRAGMA busy_timeout=2000")
    c.row_factory = sqlite3.Row
    return c

def q(sql, *a):
    c = conn()
    try:
        return [dict(r) for r in c.execute(sql, a)]
    finally:
        c.close()

def valid_job_id(jid):
    return isinstance(jid, str) and bool(JOB_ID_RE.match(jid))

def find_job_dir(job_id):
    if not valid_job_id(job_id):
        return None
    for date_dir in sorted((x for x in RUNS.iterdir() if x.is_dir()), reverse=True):
        d = date_dir / job_id
        if d.is_dir():
            return d
    return None

def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return None

def label_for(key, filled_doc):
    """Best-effort label: needs_human entry, handoff.md, else key."""
    for nh in (filled_doc or {}).get("needs_human") or []:
        if nh.get("field") == key and nh.get("label"):
            return nh["label"]
    fdoc = filled_doc or {}
    jd = find_job_dir(fdoc.get("job_id") or "")
    if jd:
        try:
            md = (jd / "handoff.md").read_text(encoding="utf-8", errors="replace")
        except Exception:
            md = ""
        m = re.search(r"^- %s \((.+?)\):" % re.escape(key), md, re.M)
        if m:
            return m.group(1)
    return key

def rev_path(job_id):
    if not valid_job_id(job_id):
        raise ValueError("bad job_id")
    return REVISIONS / (job_id + ".json")

def load_revisions(job_id):
    try:
        return read_json(rev_path(job_id)) or {}
    except ValueError:
        raise
    except Exception:
        return {}

def save_revision(job_id, key, value):
    if not valid_job_id(job_id):
        raise ValueError("bad job_id")
    if not isinstance(key, str) or not key.strip():
        raise ValueError("bad key")
    if not isinstance(value, str):
        raise ValueError("bad value")
    p = rev_path(job_id)
    with _REV_LOCK:
        REVISIONS.mkdir(parents=True, exist_ok=True)
        cur = read_json(p) if p.is_file() else {}
        if not isinstance(cur, dict):
            cur = {}
        cur[key] = value[:4000]
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(cur, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(p)
    return cur

class H(http.server.BaseHTTPRequestHandler):
    def _json(self, obj, code=200):
        b = json.dumps(obj, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def _html(self, s):
        b = s.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        self._conf_cache = {}
        p = urllib.parse.urlparse(self.path)
        path = p.path
        if path == "/":
            self._html(PAGE)
        elif path == "/api/applications":
            rows = q("""SELECT a.application_id, a.job_id, j.company, j.role, a.channel, a.status,
                            a.drafted_at AS created_at, a.submitted_at, j.gate_score, j.apply_url
                     FROM applications a LEFT JOIN jobs j ON j.job_id=a.job_id
                     ORDER BY a.application_id DESC LIMIT 500""")
            for _r in rows:
                _c, _ = resolve_confidence(_r.get("job_id"), getattr(self, "_conf_cache", None))
                _r["confidence"] = _c
                _p = _c.get("pct") if isinstance(_c, dict) else None
                _r["confidence_pct"] = int(_p) if isinstance(_p, (int, float)) and not isinstance(_p, bool) else None
            self._json(rows)
        elif path == "/api/stats":
            by_status = q("SELECT status, COUNT(*) AS n FROM applications GROUP BY status")
            by_channel = q("SELECT channel, COUNT(*) AS n FROM applications GROUP BY channel")
            self._json({"by_status": by_status, "by_channel": by_channel})
        elif path == "/api/events":
            qs = urllib.parse.parse_qs(p.query)
            try:
                lim = max(1, min(500, int(qs.get("limit", ["50"])[0])))
            except ValueError:
                lim = 50
            rows = q("""SELECT event_id, ts, job_id, application_id, kind, detail
                        FROM events ORDER BY event_id DESC LIMIT ?""", lim)
            self._json(rows)
        elif path.startswith("/api/confidence/"):
            _jid = urllib.parse.unquote(path[len("/api/confidence/"):])
            if not valid_job_id(_jid):
                self._json({"error": "invalid job_id"}, 404); return
            _has = False
            if RUNS.is_dir():
                for _dd in RUNS.iterdir():
                    if _dd.is_dir() and (_dd / _jid).is_dir():
                        _has = True; break
            if not _has:
                self._json({"error": "no run found"}, 404); return
            _c, _src = resolve_confidence(_jid, getattr(self, "_conf_cache", None))
            self._json({"job_id": _jid, "confidence": _c, "source_path": _src})
        elif path == "/api/revisions":
            out = []
            if REVISIONS.is_dir():
                for f in sorted(REVISIONS.glob("*.json")):
                    jid = f.stem
                    if valid_job_id(jid):
                        out.append({"job_id": jid, "keys": sorted((read_json(f) or {}).keys())})
            self._json(out)
        elif path.startswith("/artifacts/"):
            parts = path[len("/artifacts/"):].split("/")
            if len(parts) != 2:
                self.send_error(404); return
            job_id, fname = urllib.parse.unquote(parts[0]), urllib.parse.unquote(parts[1])
            if fname not in ALLOWED or "/" in job_id or "\\" in job_id or ".." in job_id or not valid_job_id(job_id):
                self.send_error(400); return
            target = None
            for d in sorted((x for x in RUNS.iterdir() if x.is_dir()), reverse=True):
                cand = d / job_id / fname
                if cand.is_file():
                    target = cand; break
            if target is None:
                self.send_error(404); return
            data = target.read_bytes()
            ct = "application/json" if fname.endswith(".json") else ("text/plain" if fname.endswith(".md") or fname.endswith(".jsonl") else "image/png")
            self.send_response(200)
            self.send_header("Content-Type", ct)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        else:
            m = re.match(r"^/api/(qa|revisions|form-fields)/([A-Za-z0-9_-]+)$", path)
            if not m:
                self.send_error(404)
                return
            kind, jid = m.group(1), m.group(2)
            if not valid_job_id(jid):
                self._json({"error": "invalid job_id"}, 400)
                return
            if kind == "qa":
                r = build_qa(jid)
                self._json(r if r is not None else {"error": "no run found"}, 404 if r is None else 200)
            elif kind == "revisions":
                self._json(load_revisions(jid))
            elif kind == "form-fields":
                r = form_fields_cached(jid)
                if r is None:
                    self._json({"error": "no run found"}, 404)
                else:
                    self._json(r)

    def do_POST(self):
        p = urllib.parse.urlparse(self.path)
        m = re.match(r"^/api/revision/([A-Za-z0-9_-]+)$", p.path)
        if not m:
            self.send_error(404)
            return
        jid = m.group(1)
        if not valid_job_id(jid):
            self._json({"error": "invalid job_id"}, 400)
            return
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = 0
        if n <= 0 or n > 100000:
            self._json({"error": "bad body"}, 400)
            return
        try:
            body = json.loads(self.rfile.read(n))
        except Exception:
            self._json({"error": "bad json"}, 400)
            return
        try:
            revs = save_revision(jid, body.get("key"), body.get("value"))
        except ValueError as e:
            self._json({"error": str(e)}, 400)
            return
        eff = {}
        r = build_qa(jid)
        if r:
            eff = r["effective"]
        self._json({"ok": True, "revision": revs, "effective": eff})

    def log_message(self, *a):
        pass


def resolve_confidence(job_id, cache=None):
    if not valid_job_id(job_id):
        return (None, None)
    if cache is not None and job_id in cache:
        return cache[job_id]
    cands = []
    if RUNS.is_dir():
        for dd in RUNS.iterdir():
            if not dd.is_dir():
                continue
            d = dd / job_id
            if not d.is_dir():
                continue
            for name in ("confidence.json", "filled.json"):
                p = d / name
                if p.is_file():
                    try:
                        cands.append((p, os.path.getmtime(p)))
                    except OSError:
                        pass
    if not cands:
        res = (None, None)
        if cache is not None:
            cache[job_id] = res
        return res
    cands.sort(key=lambda x: x[1], reverse=True)
    chosen = cands[0][0]
    conf = None
    try:
        data = read_json(chosen)
        if isinstance(data, dict):
            if chosen.name == "confidence.json":
                conf = data
            else:
                c = data.get("confidence")
                if isinstance(c, dict):
                    conf = c
    except Exception:
        conf = None
    if isinstance(conf, dict) and conf.get("pct") is None:
        conf = None
    res = (conf, str(chosen) if conf is not None else None)
    if cache is not None:
        cache[job_id] = res
    return res

def build_qa(job_id):
    d = find_job_dir(job_id)
    if not d:
        return None
    doc = read_json(d / "filled.json") or {}
    filled = doc.get("filled")
    if isinstance(filled, dict):
        filled_rows = [{"key": k, "label": label_for(k, doc), "value": v, "source": "filled"}
                       for k, v in filled.items()]
    elif isinstance(filled, list):  # legacy shape: [{label, value}]
        filled_rows = [{"key": r.get("label") or "", "label": r.get("label") or "",
                        "value": r.get("value"), "source": "filled"} for r in filled]
    else:
        filled_rows = []
    needs = [{"field": n.get("field") or "", "label": n.get("label") or n.get("field") or "",
              "reason": (n.get("error") or n.get("reason") or "").strip()}
             for n in (doc.get("needs_human") or [])]
    meta = {}
    for k in ("company", "role", "ats"):
        meta[k] = doc.get(k)
    if not meta["company"] or not meta["role"]:
        try:
            r = q("SELECT company, role, ats FROM jobs WHERE job_id=?", job_id)
            if r:
                meta = {k: meta.get(k) or r[0].get(k) for k in meta}
        except Exception:
            pass
    revs = load_revisions(job_id)
    eff = {r["key"]: r["value"] for r in filled_rows}
    eff.update(revs)
    return {"job_id": job_id, **meta,
            "apply_url": doc.get("apply_url") or doc.get("url"),
            "stopped": doc.get("stopped"), "filled": filled_rows,
            "needs_human": needs, "revision": revs, "effective": eff}

def scrape_form_fields(job_id):
    d = find_job_dir(job_id)
    if not d:
        return None
    doc = read_json(d / "filled.json") or {}
    url = doc.get("apply_url") or doc.get("url")
    if not url:
        return {"error": "no apply_url", "fields": []}
    from playwright.sync_api import sync_playwright
    fields = []
    err = None
    try:
        with sync_playwright() as pw:
            b = pw.chromium.launch(headless=True)
            try:
                pg = b.new_page()
                pg.goto(url, wait_until="domcontentloaded", timeout=20000)
                try:
                    pg.wait_for_load_state("networkidle", timeout=8000)
                except Exception:
                    pass
                js = """() => [...document.querySelectorAll('input,textarea,select')].map(el => {
                  const lab = el.labels && el.labels[0] ? el.labels[0].textContent.trim()
                    : (el.closest('label') ? el.closest('label').textContent.trim() : '');
                  let val = el.value || '';
                  if (el.type === 'checkbox' || el.type === 'radio')
                    val = el.checked ? 'checked' : '';
                  if (el.tagName === 'SELECT')
                    val = el.selectedOptions.length ? [...el.selectedOptions].map(o=>o.textContent.trim()).join(', ') : '';
                  return {label: lab, name: el.name || el.id || '', type: el.tagName.toLowerCase(),
                          required: !!(el.required || el.getAttribute('aria-required') === 'true'),
                          current_value: val,
                          options: el.tagName === 'SELECT' ? [...el.options].map(o=>o.textContent.trim()) : undefined};
                }).filter(f => f.type !== 'hidden')"""
                fields = pg.evaluate(js)
            finally:
                b.close()
    except Exception as e:
        err = str(e)[:300]
    if err is not None:
        return {"error": err, "fields": fields}
    return {"fields": fields}

def form_fields_cached(job_id):
    now = time.time()
    with _FORM_LOCK:
        ent = _FORM_CACHE.get(job_id)
        if ent and now - ent[0] < 300:
            return ent[1]
    res = scrape_form_fields(job_id)
    with _FORM_LOCK:
        _FORM_CACHE[job_id] = (now, res)
    return res

PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>job-autopilot applications</title>
<meta name="description" content="Read-only view of job-autopilot applications, the answers each run filled, and the ones that still need you.">
<style>
/* Hallmark · shape: app screen · genre: modern-minimal · theme: custom · posture: restrained · surface: tinted · job: triage applications and revise the answers a run left blank · states: loading, empty, error, full · contrast: pass · v2.0.0 */
/* Hallmark · pre-emit critique: P4 H5 E5 S5 R4 V4 · weakest: R, the confidence bar is the one ornament kept; it earns the room as the only figure that compares */
:root{
  --color-paper:oklch(98.4% 0.004 255);
  --color-surface:oklch(99% 0.005 255);
  --color-well:oklch(96.4% 0.006 255);
  --color-well-2:oklch(94% 0.009 255);
  --color-ink:oklch(25% 0.014 255);
  --color-ink-2:oklch(38% 0.013 255);
  --color-muted:oklch(46% 0.014 255);
  --color-border:oklch(88.5% 0.008 255);
  --color-rule:oklch(91% 0.007 255);
  --color-accent:oklch(51% 0.14 255);
  --color-accent-2:oklch(45% 0.13 255);
  --color-accent-ink:oklch(99% 0.008 255);
  --color-selected:oklch(93.5% 0.04 255);
  --color-focus:oklch(50% 0.14 255);
  --color-ok:oklch(44% 0.11 150);
  --color-warn:oklch(44% 0.09 70);
  --color-warn-soft:oklch(95.5% 0.035 85);
  --color-warn-ink:oklch(35% 0.06 70);
  --color-bad:oklch(45% 0.14 25);
  --color-bad-soft:oklch(95.5% 0.03 25);
  --shadow-panel:0 1px 2px oklch(20% 0.01 255 / 0.06);
  --font-body:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
  --font-mono:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
  --space-2xs:4px;--space-xs:8px;--space-sm:12px;--space-md:16px;--space-lg:24px;--space-xl:40px;
  --radius-sm:6px;--radius:10px;--radius-pill:999px;
  --dur-micro:120ms;--dur-short:220ms;--ease-out:cubic-bezier(.2,.8,.3,1);
  --bar-h:60px;--sticky-top:72px;--drawer-w:440px;
}
*{box-sizing:border-box}
html{overflow-x:clip;scrollbar-color:var(--color-muted) var(--color-paper)}
body{overflow-x:clip;margin:0;background:var(--color-paper);color:var(--color-ink);font-family:var(--font-body);font-size:16px;line-height:1.5}
::selection{background:var(--color-selected);color:var(--color-ink)}
.sr-only{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}
h1,h2,h3{min-width:0;overflow-wrap:break-word;text-wrap:balance}
.topbar{position:sticky;top:0;z-index:200;display:flex;align-items:center;gap:var(--space-md);flex-wrap:wrap;padding:var(--space-sm) var(--space-md);background:var(--color-surface);border-bottom:1px solid var(--color-border)}
.topbar h1{margin:0;font-size:17px;font-weight:650}
.topbar .meta{display:flex;align-items:center;gap:var(--space-md);margin-inline-start:auto;font-size:13px;color:var(--color-muted)}
.wrap{max-width:1440px;margin:0 auto;padding:var(--space-md) var(--space-md) var(--space-xl)}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(176px,1fr));gap:var(--space-lg);padding:var(--space-lg) 0;border-bottom:1px solid var(--color-rule)}
.stat-label{margin:0;font-size:13px;font-weight:600;color:var(--color-ink-2)}
.stat-value{margin:var(--space-2xs) 0 0;font-size:28px;font-weight:650;line-height:1.1;font-variant-numeric:tabular-nums}
.stat-note{margin:var(--space-2xs) 0 0;font-size:12px;color:var(--color-muted)}
.workbench{display:grid;grid-template-columns:minmax(0,1fr);gap:var(--space-lg);align-items:start;margin-top:var(--space-md)}
@media(min-width:1024px){.workbench.open{grid-template-columns:minmax(0,1fr) var(--drawer-w)}}
.region{min-width:0;margin-top:var(--space-lg);padding-top:var(--space-md);border-top:1px solid var(--color-rule)}
.region-head{display:flex;align-items:baseline;justify-content:space-between;gap:var(--space-sm);flex-wrap:wrap}
.region-head h2{margin:0;font-size:16px;font-weight:650}
.count{font-size:13px;color:var(--color-muted);font-variant-numeric:tabular-nums}
.hint{margin:var(--space-2xs) 0 0;font-size:12px;color:var(--color-muted)}
.mono{font-family:var(--font-mono);font-size:12px;font-variant-numeric:tabular-nums}
.toolbar{display:flex;align-items:center;gap:var(--space-xs);flex-wrap:wrap;margin:var(--space-sm) 0}
.toolbar input,.toolbar select{height:36px;padding:0 var(--space-sm);border:1px solid var(--color-border);border-radius:var(--radius-sm);background:var(--color-surface);color:var(--color-ink);font-family:inherit;font-size:14px;outline:2px solid transparent;outline-offset:-3px}
.toolbar input[type=search]{flex:1 1 200px;min-width:0;max-width:320px;caret-color:var(--color-accent)}
.toolbar select{max-width:200px}
.chips{display:flex;gap:var(--space-xs);flex-wrap:wrap;margin-bottom:var(--space-sm)}
.chip{min-height:32px;padding:0 var(--space-sm);border:1px solid var(--color-border);border-radius:var(--radius-pill);background:var(--color-surface);color:var(--color-ink-2);font-family:inherit;font-size:13px;cursor:pointer;transition:background-color var(--dur-micro) var(--ease-out)}
.chip .n{margin-inline-start:var(--space-2xs);color:var(--color-muted);font-variant-numeric:tabular-nums}
.chip.active{background:var(--color-selected);border-color:var(--color-accent);color:var(--color-ink)}
.btn{min-height:32px;padding:0 var(--space-sm);border:1px solid var(--color-border);border-radius:var(--radius-sm);background:transparent;color:var(--color-ink);font-family:inherit;font-size:13px;cursor:pointer;transition:background-color var(--dur-micro) var(--ease-out),transform var(--dur-micro) var(--ease-out)}
.btn.primary{background:var(--color-accent);border-color:var(--color-accent);color:var(--color-accent-ink);font-weight:600}
.btn:disabled{opacity:.55;cursor:not-allowed}
.btn:active{transform:translateY(1px)}
@media(hover:hover){
  .btn:hover,.chip:hover,tbody tr:hover{background:var(--color-well)}
  .btn.primary:hover{background:var(--color-accent-2)}
  .row-title:hover{text-decoration:underline;text-underline-offset:3px}
}
:where(a,button,input,select,textarea,summary,[tabindex]):focus-visible{outline:2px solid var(--color-focus);outline-offset:2px}
:where(input,select,textarea):focus-visible{outline-offset:-3px}
table{width:100%;border-collapse:collapse;font-size:14px}
th,td{padding:var(--space-xs) var(--space-sm);text-align:left;vertical-align:top;overflow-wrap:anywhere}
th{position:sticky;top:var(--bar-h);z-index:100;background:var(--color-paper);color:var(--color-ink-2);font-size:12px;font-weight:600;white-space:nowrap;box-shadow:inset 0 -1px 0 var(--color-border)}
tbody tr{border-top:1px solid var(--color-rule);cursor:pointer}
tbody tr.selected{background:var(--color-selected)}
.row-title{padding:0;border:0;background:none;color:var(--color-ink);font-family:inherit;font-size:inherit;font-weight:600;text-align:left;cursor:pointer}
.pager{display:flex;align-items:center;gap:var(--space-xs);flex-wrap:wrap;margin-top:var(--space-sm);font-size:13px;color:var(--color-muted);font-variant-numeric:tabular-nums}
.status-pill{display:inline-block;padding:2px var(--space-xs);border:1px solid var(--color-border);border-radius:var(--radius-pill);background:var(--color-well);color:var(--color-ink-2);font-size:12px;font-weight:600;white-space:nowrap}
.s-submitted,.s-submitted_auto,.s-submitted_human{color:var(--color-ok)}
.s-prefilled,.s-shortlisted{color:var(--color-accent)}
.s-drafted{color:var(--color-warn)}
.s-withdrawn{color:var(--color-bad)}
.s-discovered,.s-gated{color:var(--color-muted)}
.conf-pill{display:inline-flex;align-items:center;gap:var(--space-2xs);color:var(--color-ink-2);font-size:12px;font-weight:600;font-variant-numeric:tabular-nums}
.conf-bar{width:32px;height:4px;border-radius:2px;background:var(--color-well-2);overflow:hidden}
.conf-bar>span{display:block;height:100%;background:currentColor}
.tone-ok{color:var(--color-ok)}
.tone-warn{color:var(--color-warn)}
.tone-bad{color:var(--color-bad)}
.tone-muted{color:var(--color-muted)}
/* the drawer: docked right on a wide screen, a full-screen overlay under 64rem */
.drawer{display:flex;flex-direction:column;min-width:0;background:var(--color-surface);border:1px solid var(--color-border);border-radius:var(--radius);opacity:0;transform:translateX(16px);transition:transform var(--dur-short) var(--ease-out),opacity var(--dur-short) var(--ease-out)}
.drawer[hidden]{display:none}
.drawer.in{opacity:1;transform:none}
@media(min-width:1024px){
  .drawer{position:sticky;top:var(--sticky-top);max-height:calc(100dvh - var(--sticky-top) - var(--space-md));overflow-y:auto}
}
@media(max-width:1023.98px){
  html.drawer-open,body.drawer-open{overflow-y:hidden}
  .drawer{position:fixed;inset:0;width:100%;z-index:400;overflow-y:auto;border:0;border-radius:0;box-shadow:var(--shadow-panel);transform:translateX(100%);opacity:1}
  .drawer.in{transform:none}
}
.drawer-head{display:flex;align-items:flex-start;gap:var(--space-sm);padding:var(--space-md);border-bottom:1px solid var(--color-rule)}
.drawer-headmain{flex:1;min-width:0}
.drawer-head h2{margin:0;font-size:16px;font-weight:650}
.drawer-sub{margin:var(--space-2xs) 0 0;font-size:13px;color:var(--color-ink-2)}
.drawer-tags{display:flex;align-items:center;gap:var(--space-xs);flex-wrap:wrap;margin:var(--space-xs) 0 0}
.drawer-meta{font-size:12px;color:var(--color-muted)}
.drawer-body{display:flex;flex-direction:column;gap:var(--space-md);padding:var(--space-md)}
.drawer-h3{margin:0;font-size:14px;font-weight:650}
.icon-btn{position:relative;flex:0 0 auto;display:grid;place-items:center;width:32px;height:32px;border:1px solid var(--color-border);border-radius:var(--radius-sm);background:transparent;color:var(--color-ink-2);font-family:inherit;font-size:18px;line-height:1;cursor:pointer}
.icon-btn::before{content:"";position:absolute;inset:-6px}
@media(hover:hover){.icon-btn:hover{background:var(--color-well)}}
.conf{margin:0;padding:var(--space-sm);border-radius:var(--radius-sm);background:var(--color-well)}
.conf-head{display:flex;align-items:center;gap:var(--space-xs)}
.conf-pct{font-size:24px;font-weight:650;font-variant-numeric:tabular-nums}
.conf-none{margin:0;font-size:13px;color:var(--color-muted)}
.conf-meta{margin:var(--space-xs) 0 0;font-size:12px;color:var(--color-ink-2)}
.conf-list{margin:var(--space-2xs) 0 0;padding-inline-start:var(--space-md);font-size:13px}
.qcard{padding:var(--space-sm) 0;border-top:1px solid var(--color-rule)}
.qcard.needs{border-top-color:var(--color-bad)}
.q-label{margin:0;font-size:14px;font-weight:600}
.q-key{margin:var(--space-2xs) 0 0;font-family:var(--font-mono);font-size:11px;color:var(--color-muted)}
.q-val{margin:var(--space-xs) 0 0;padding:var(--space-xs);border-radius:var(--radius-sm);background:var(--color-well);font-size:13px}
.q-valtag{display:block;font-size:11px;color:var(--color-muted)}
.qcard textarea{width:100%;min-height:96px;margin-top:var(--space-xs);padding:var(--space-xs);border:1px solid var(--color-border);border-radius:var(--radius-sm);background:var(--color-surface);color:var(--color-ink);font-family:inherit;font-size:13px;outline:2px solid transparent;outline-offset:-3px;resize:vertical;caret-color:var(--color-accent)}
.q-actions{display:flex;gap:var(--space-xs);align-items:center;flex-wrap:wrap;margin-top:var(--space-xs)}
.q-reason summary{cursor:pointer;font-size:12px;color:var(--color-bad)}
.badge{display:inline-block;margin-inline-start:var(--space-2xs);padding:2px var(--space-xs);border-radius:var(--radius-pill);font-size:11px;font-weight:600}
.badge.edited{background:var(--color-warn-soft);color:var(--color-warn-ink)}
.badge.needs{background:var(--color-bad-soft);color:var(--color-bad)}
.status-live{font-size:12px;color:var(--color-muted)}
.files{display:flex;flex-direction:column;gap:var(--space-2xs);margin:0;padding:0;list-style:none;font-size:13px}
.filelink{color:var(--color-ink);text-decoration:underline;text-underline-offset:3px}
.empty{margin:0;padding:var(--space-lg);text-align:center;color:var(--color-muted);font-size:14px}
.error-box{margin:0;padding:var(--space-sm);border:1px solid var(--color-bad);border-radius:var(--radius-sm);background:var(--color-bad-soft);color:var(--color-bad);font-size:13px}
.error-box p{margin:0 0 var(--space-xs)}
.spinner{display:inline-block;width:16px;height:16px;margin-inline-end:var(--space-xs);border:2px solid var(--color-well-2);border-top-color:var(--color-accent);border-radius:50%;animation:spin 1s linear infinite;vertical-align:middle}
.skeleton{height:14px;margin:var(--space-xs) 0;border-radius:var(--radius-sm);background:linear-gradient(90deg,var(--color-well) 25%,var(--color-well-2) 50%,var(--color-well) 75%);background-size:200% 100%;animation:shimmer 1.4s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}
@keyframes shimmer{0%{background-position:200% 0}100%{background-position:-200% 0}}
.well{max-height:240px;overflow:auto;padding:var(--space-sm);border-radius:var(--radius-sm);background:var(--color-well);font-family:var(--font-mono);font-size:12px}
.ev{padding:2px 0;overflow-wrap:anywhere}
.ev-ts{color:var(--color-muted)}
.ev-kind{font-weight:600}
@media(prefers-reduced-motion:reduce){
  .spinner,.skeleton{animation:none}
  .btn,.chip,.drawer{transition:none}
}
/* a docked drawer leaves about 530px of table at 1024, so the three quiet columns step out */
@media(min-width:768px) and (max-width:1279.98px){
  .apps-table th:nth-child(4),.apps-table td:nth-child(4),.apps-table th:nth-child(6),.apps-table td:nth-child(6),.apps-table th:nth-child(7),.apps-table td:nth-child(7){display:none}
}
@media(max-width:767.98px){
  .apps-table thead{display:none}
  .apps-table,.apps-table tbody,.apps-table tr,.apps-table td{display:block;width:100%}
  .apps-table tr{margin-bottom:var(--space-sm);padding:var(--space-sm);border:1px solid var(--color-border);border-radius:var(--radius-sm);background:var(--color-surface)}
  .apps-table td{padding:var(--space-2xs) 0;border:0}
  .apps-table td::before{content:attr(data-label);display:block;font-size:11px;font-weight:600;color:var(--color-muted)}
}
@media(pointer:coarse){.btn,.chip,.icon-btn,.toolbar input,.toolbar select{min-height:48px}}
</style></head><body>
<header class="topbar">
  <h1>job-autopilot</h1>
  <div class="meta">
    <span id="lastupd">Reading the tracker</span>
    <span id="refreshtxt">starting</span>
  </div>
</header>
<div class="wrap">
  <noscript><p class="empty">This dashboard reads its tables from the local JSON API, so it needs JavaScript.</p></noscript>
  <section class="stats" id="kpis" aria-label="Totals"></section>
  <div class="workbench" id="workbench">
    <section class="region" aria-labelledby="appshead">
      <div class="region-head">
        <h2 id="appshead">Applications</h2>
        <span class="count" id="appscount"></span>
      </div>
      <div class="toolbar">
        <label class="sr-only" for="qsearch">Search applications</label>
        <input id="qsearch" type="search" name="q" placeholder="Search company or role" autocomplete="off">
        <label class="sr-only" for="sortsel">Sort applications</label>
        <select id="sortsel" name="sort">
          <option value="newest">Newest first</option>
          <option value="company">Company</option>
          <option value="status">Status</option>
          <option value="confidence">Confidence</option>
        </select>
        <label class="sr-only" for="pagesel">Rows per page</label>
        <select id="pagesel" name="size"><option>25</option><option selected>50</option><option>100</option></select>
      </div>
      <div class="chips" id="statuschips" role="group" aria-label="Filter by status"></div>
      <div id="appsWrap"></div>
    </section>
    <aside class="drawer" id="drawer" hidden aria-labelledby="drawertitle"></aside>
  </div>
  <section class="region" aria-labelledby="ffhead">
    <div class="region-head">
      <h2 id="ffhead">Live form fields</h2>
      <span class="count" id="ffstatus" role="status"></span>
    </div>
    <div class="toolbar">
      <button class="btn" type="button" id="ffbtn">Read the live form</button>
      <span class="hint">Opens the apply page in a headless browser, up to 20 seconds.</span>
    </div>
    <div id="ffout"></div>
  </section>
  <section class="region" aria-labelledby="evhead">
    <div class="region-head"><h2 id="evhead">Live events</h2></div>
    <div class="well" id="events"></div>
  </section>
</div>
<script>
const $=id=>document.getElementById(id);
const esc=s=>(s==null?'':''+s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const jsq=s=>String(s==null?'':s).replace(/\\/g,'\\\\').replace(/'/g,"\\'");
const revId=k=>'rev_'+btoa(unescape(encodeURIComponent(k))).replace(/=+$/,'');
const stId=k=>'st_'+k.replace(/[^A-Za-z0-9]/g,'');
let APPS=[],SELECTED='',QUERY='',STATUS_FILTER='all',SORT='newest',PAGE_N=1,PAGE_SIZE=50;
let TRIGGER=null,CLOSE_T=0,SIG='';
function emptyState(msg){return '<p class="empty">'+esc(msg)+'</p>';}
function errorState(msg,cb){return '<div class="error-box"><p>'+esc(msg)+'</p><button class="btn" type="button" onclick="'+cb+'">Retry</button></div>';}
function appsSkeleton(n){return '<div class="skeleton"></div>'.repeat(n||6);}
async function fetchJson(url){
  const r=await fetch(url);
  if(!r.ok){
    let m='Request failed ('+r.status+')';
    try{const j=await r.json();if(j&&j.error) m=j.error;}catch(e){}
    throw new Error(m);
  }
  return r.json();
}
function confPct(c){if(!c||c.pct==null||c.pct==='') return null;const n=Math.round(Number(c.pct));return isNaN(n)?null:Math.max(0,Math.min(100,n));}
function confTone(p){return p>=80?'tone-ok':(p>=50?'tone-warn':'tone-bad');}
function confText(c){
  const missing=(c&&c.missing?c.missing:[]).filter(m=>m!=null&&m!=='');
  let t='Method '+(c&&c.method||'unknown')+'. '+(c&&c.filled!=null?c.filled:'?')+' of '+(c&&c.total!=null?c.total:'?')+' fields filled from the profile.';
  if(missing.length) t+=' Still needs you: '+missing.join(', ')+'.';
  return t;
}
function confPill(c){
  const p=confPct(c);
  if(p==null) return '<span class="conf-pill tone-muted" title="No confidence data recorded" aria-label="Confidence: no data">no data</span>';
  const t=confText(c);
  return '<span class="conf-pill '+confTone(p)+'" title="'+esc(t)+'" aria-label="Confidence '+p+' percent. '+esc(t)+'">'+p+'%<span class="conf-bar" aria-hidden="true"><span style="width:'+p+'%"></span></span></span>';
}
function confSummary(c){
  const p=confPct(c);
  if(p==null) return '<div class="conf"><p class="conf-none">No confidence data for this application. A run writes confidence.json or a confidence block in filled.json; neither is here.</p></div>';
  const missing=(c.missing||[]).filter(m=>m!=null&&m!=='');
  let h='<div class="conf"><div class="conf-head '+confTone(p)+'"><span class="conf-pct">'+p+'%</span><span class="conf-bar" aria-hidden="true"><span style="width:'+p+'%"></span></span></div>'
    +'<p class="conf-meta">'+esc(confText(c))+(c.channel?' Read from the '+esc(c.channel)+' channel.':'')+'</p>';
  if(missing.length){
    h+='<p class="conf-meta">Not filled from the profile:</p><ul class="conf-list">'+missing.slice(0,8).map(m=>'<li>'+esc(m)+'</li>').join('')+'</ul>';
    if(missing.length>8) h+='<p class="conf-meta">and '+(missing.length-8)+' more.</p>';
  }
  return h+'</div>';
}
async function confFor(jid){
  try{
    const r=await fetch('/api/confidence/'+encodeURIComponent(jid));
    if(!r.ok) return null;
    const j=await r.json();
    return j.confidence||null;
  }catch(e){return null;}
}
function renderKPIs(apps){
  const counts={};
  apps.forEach(a=>{counts[a.status]=(counts[a.status]||0)+1;});
  const ready=(counts.prefilled||0)+(counts.shortlisted||0);
  const emailed=apps.filter(a=>a.channel==='email').length;
  const companies=new Set(apps.map(a=>a.company).filter(Boolean)).size;
  const rows=apps.filter(a=>confPct(a.confidence)!=null);
  const avg=rows.length?Math.round(rows.reduce((s,a)=>s+confPct(a.confidence),0)/rows.length):null;
  const defs=[
    ['Submitted',counts.submitted||0,'Confirmed on the company page.'],
    ['Ready to submit',ready,'Prefilled or shortlisted, waiting on the final submit.'],
    ['Sent by email',emailed,'Applications delivered through the email channel.'],
    ['Companies',companies,'Distinct companies in the tracker.'],
    ['Avg confidence',avg==null?'No data':avg+'%',rows.length+' of '+apps.length+' rows recorded confidence.']
  ];
  $('kpis').innerHTML=defs.map(d=>'<div class="stat"><p class="stat-label">'+esc(d[0])+'</p><p class="stat-value">'+esc(d[1])+'</p><p class="stat-note">'+esc(d[2])+'</p></div>').join('');
}
function statusCounts(apps){const c={};apps.forEach(a=>{c[a.status]=(c[a.status]||0)+1;});return c;}
function chipHtml(value,label,n){
  const on=STATUS_FILTER===value;
  return '<button type="button" class="chip'+(on?' active':'')+'" data-s="'+esc(value)+'" aria-pressed="'+(on?'true':'false')+'">'+esc(label)+' <span class="n">'+esc(n)+'</span></button>';
}
function renderChips(){
  const c=statusCounts(APPS),keys=Object.keys(c).sort();
  let h=chipHtml('all','All',APPS.length);
  keys.forEach(s=>{h+=chipHtml(s,s.replace(/_/g,' '),c[s]);});
  if(STATUS_FILTER!=='all'&&!keys.includes(STATUS_FILTER)) h+=chipHtml(STATUS_FILTER,STATUS_FILTER.replace(/_/g,' '),0);
  $('statuschips').innerHTML=h;
}
function setFilter(s){STATUS_FILTER=s;PAGE_N=1;renderApps();syncURL();}
function sortApps(list){
  const arr=[...list];
  if(SORT==='company') arr.sort((a,b)=>String(a.company||'').localeCompare(String(b.company||'')));
  else if(SORT==='status') arr.sort((a,b)=>String(a.status||'').localeCompare(String(b.status||'')));
  else if(SORT==='confidence') arr.sort((a,b)=>{
    const x=confPct(a.confidence),y=confPct(b.confidence);
    if(x==null&&y==null) return 0;
    if(x==null) return 1;
    if(y==null) return -1;
    return y-x;
  });
  return arr;
}
function renderApps(){
  renderChips();
  const wrap=$('appsWrap');
  let list=APPS.filter(a=>{
    if(STATUS_FILTER!=='all'&&a.status!==STATUS_FILTER) return false;
    if(QUERY&&!(String(a.company||'').toLowerCase().includes(QUERY)||String(a.role||'').toLowerCase().includes(QUERY))) return false;
    return true;
  });
  list=sortApps(list);
  const total=list.length,pages=Math.max(1,Math.ceil(total/PAGE_SIZE));
  if(PAGE_N>pages)PAGE_N=pages;
  if(PAGE_N<1)PAGE_N=1;
  const start=(PAGE_N-1)*PAGE_SIZE,end=Math.min(start+PAGE_SIZE,total);
  $('appscount').textContent=total+(total===1?' application':' applications')+(total===APPS.length?'':' filtered from '+APPS.length);
  if(!total){
    wrap.innerHTML=emptyState(APPS.length?'No application matches this search or filter.':'No applications recorded yet. A run adds a row here when it drafts an application.');
    return;
  }
  let html='<table class="apps-table"><thead><tr><th>ID</th><th>Company</th><th>Role</th><th>Channel</th><th>Status</th><th>Created</th><th>Submitted</th><th>Confidence</th><th>Gate</th></tr></thead><tbody>';
  list.slice(start,end).forEach(a=>{
    const jid=a.job_id||'',on=!!jid&&jid===SELECTED;
    html+='<tr class="'+(on?'selected':'')+'" data-job="'+esc(jid)+'">'
      +'<td class="mono" data-label="ID">'+esc(a.application_id)+'</td>'
      +'<td data-label="Company"><button class="row-title" type="button" data-job="'+esc(jid)+'" aria-expanded="'+(on?'true':'false')+'" aria-controls="drawer">'+esc(a.company||jid||'unknown')+'</button></td>'
      +'<td data-label="Role">'+esc(a.role||'')+'</td>'
      +'<td data-label="Channel">'+esc(a.channel||'')+'</td>'
      +'<td data-label="Status"><span class="status-pill s-'+esc(a.status||'')+'">'+esc(String(a.status||'unknown').replace(/_/g,' '))+'</span></td>'
      +'<td class="mono" data-label="Created">'+esc(a.created_at||'')+'</td>'
      +'<td class="mono" data-label="Submitted">'+esc(a.submitted_at||'')+'</td>'
      +'<td data-label="Confidence">'+confPill(a.confidence)+'</td>'
      +'<td data-label="Gate">'+esc(a.gate_score==null?'':a.gate_score)+'</td></tr>';
  });
  html+='</tbody></table>';
  html+='<div class="pager"><button class="btn" type="button" onclick="goPage('+(PAGE_N-1)+')"'+(PAGE_N<=1?' disabled':'')+'>Prev</button>'
    +'<span>Page '+PAGE_N+' of '+pages+', showing '+(total?start+1:0)+' to '+end+' of '+total+'</span>'
    +'<button class="btn" type="button" onclick="goPage('+(PAGE_N+1)+')"'+(PAGE_N>=pages?' disabled':'')+'>Next</button></div>';
  wrap.innerHTML=html;
}
function goPage(n){PAGE_N=n;renderApps();syncURL();}
function selectApp(jid,triggerEl){
  if(!jid) return;
  const d=$('drawer');
  if(jid===SELECTED&&!d.hidden){closeDrawer();return;}
  SELECTED=jid;
  TRIGGER=triggerEl||null;
  openDrawer();
}
function openDrawer(){
  const d=$('drawer');
  const app=APPS.find(a=>a.job_id===SELECTED)||{job_id:SELECTED};
  CLOSE_T++;
  d.innerHTML=drawerShell(app);
  d.hidden=false;
  $('workbench').classList.add('open');
  document.documentElement.classList.add('drawer-open');
  document.body.classList.add('drawer-open');
  renderApps();
  const row=document.querySelector('.row-title[data-job="'+esc(SELECTED)+'"]');
  if(row) TRIGGER=row;
  requestAnimationFrame(()=>requestAnimationFrame(()=>d.classList.add('in')));
  const btn=d.querySelector('[data-close]');
  if(btn) btn.focus();
  syncURL();
  loadDrawerContent(SELECTED);
  $('ffout').innerHTML='';
  $('ffstatus').textContent='';
}
function closeDrawer(refocus){
  const d=$('drawer');
  if(d.hidden) return;
  const t=++CLOSE_T,jid=SELECTED;
  SELECTED='';
  d.classList.remove('in');
  $('workbench').classList.remove('open');
  document.documentElement.classList.remove('drawer-open');
  document.body.classList.remove('drawer-open');
  setTimeout(()=>{if(t===CLOSE_T){d.hidden=true;d.innerHTML='';}},240);
  renderApps();
  syncURL();
  if(refocus!==false){
    const row=document.querySelector('.row-title[data-job="'+esc(jid)+'"]')||TRIGGER;
    if(row&&row.focus) row.focus();
  }
}
function drawerShell(app){
  const jid=app.job_id||'';
  return '<div class="drawer-head"><div class="drawer-headmain">'
    +'<h2 id="drawertitle">'+esc(app.company||jid||'Application')+'</h2>'
    +'<p class="drawer-sub">'+esc(app.role||'No role recorded')+'</p>'
    +'<p class="drawer-tags"><span class="status-pill s-'+esc(app.status||'')+'">'+esc(String(app.status||'unknown').replace(/_/g,' '))+'</span><span class="drawer-meta" id="drawerats"></span></p>'
    +'</div><button class="icon-btn" type="button" data-close aria-label="Close panel" title="Close" onclick="closeDrawer()">&times;</button></div>'
    +'<div class="drawer-body" id="drawerbody">'+appsSkeleton(4)+'</div>';
}
function runFiles(jid){
  const base='/artifacts/'+encodeURIComponent(jid)+'/';
  return '<p class="hint">Written by the run. A missing file answers 404.</p><ul class="files">'
    +'<li><a class="filelink" href="'+base+'form.png" target="_blank" rel="noopener">Filled form screenshot (form.png)</a></li>'
    +'<li><a class="filelink" href="'+base+'steps.jsonl" target="_blank" rel="noopener">Steps log (steps.jsonl)</a></li>'
    +'<li><a class="filelink" href="'+base+'handoff.md" target="_blank" rel="noopener">Handoff note (handoff.md)</a></li>'
    +'<li><a class="filelink" href="'+base+'filled.json" target="_blank" rel="noopener">Filled answers (filled.json)</a></li>'
    +'</ul>';
}
function qcard(k,qa,revs,eff,nh){
  const row=(qa.filled||[]).find(r=>r.key===k)||{};
  const isEdited=revs[k]!=null,isNeeds=nh[k]!=null;
  const effVal=eff[k]==null?'':eff[k];
  const filledVal=row.value==null?'':row.value;
  const id=revId(k),label=esc(row.label||k);
  let h='<div class="qcard'+(isNeeds?' needs':'')+'">';
  h+='<p class="q-label">'+label+(isEdited?'<span class="badge edited">edited</span>':'')+(isNeeds?'<span class="badge needs">needs human</span>':'')+'</p>';
  h+='<p class="q-key">'+esc(k)+'</p>';
  h+='<p class="q-val"><span class="q-valtag">Filled value</span>'+esc(filledVal||'nothing')+'</p>';
  if(isNeeds) h+='<details class="q-reason"><summary>Why this needs you</summary><p class="hint">'+esc(nh[k])+'</p></details>';
  h+='<label class="sr-only" for="'+id+'">Your answer for '+label+'</label>';
  h+='<textarea id="'+id+'" data-key="'+esc(k)+'" data-eff="'+esc(effVal)+'" oninput="syncSaveBtn(\''+id+'\')">'+esc(effVal)+'</textarea>';
  h+='<div class="q-actions">'
    +'<button class="btn primary" type="button" id="btn_'+id+'" onclick="saveRev(\''+id+'\',\''+jsq(k)+'\')">Save revision</button>'
    +'<button class="btn" type="button" onclick="copyVal(this,\''+id+'\')">Copy effective value</button>'
    +'<span class="status-live" id="'+stId(k)+'" role="status"></span></div></div>';
  return h;
}
async function loadDrawerContent(jid){
  const body=$('drawerbody'),ats=$('drawerats');
  if(!body) return;
  body.innerHTML=appsSkeleton(4);
  try{
    const qa=await fetchJson('/api/qa/'+encodeURIComponent(jid));
    const conf=await confFor(jid);
    if(SELECTED!==jid) return;
    if(ats) ats.textContent='ATS '+(qa.ats||'unknown')+(qa.stopped?', the run stopped: '+qa.stopped:'');
    const revs=qa.revision||{},eff=qa.effective||{};
    const nh={};
    (qa.needs_human||[]).forEach(n=>{if(n.field) nh[n.field]=n.reason||'';});
    const keys=[...new Set([...(qa.filled||[]).map(r=>r.key),...Object.keys(revs),...Object.keys(nh)])].filter(Boolean);
    let h=confSummary(conf);
    h+='<h3 class="drawer-h3">Questions</h3>';
    h+=keys.length?keys.map(k=>qcard(k,qa,revs,eff,nh)).join('')
      :emptyState('No question recorded for this application. A run writes each answered field into filled.json; this one recorded none.');
    h+='<h3 class="drawer-h3">Run files</h3>'+runFiles(jid);
    body.innerHTML=h;
    keys.forEach(k=>syncSaveBtn(revId(k)));
  }catch(e){
    if(SELECTED!==jid) return;
    body.innerHTML=/no run found/i.test(e.message)
      ? emptyState('No run directory for this application yet. A run writes the filled answers, the confidence report and a handoff note here; nothing has been written for this one.')+runFiles(jid)
      : errorState('Could not load this application: '+e.message,'loadDrawerContent(\''+jsq(jid)+'\')');
  }
}
function syncSaveBtn(id){
  const ta=$(id),btn=$('btn_'+id);
  if(!ta||!btn) return;
  if(ta.value===(ta.dataset.eff||'')){btn.disabled=true;btn.textContent='Unchanged';}
  else {btn.disabled=false;btn.textContent='Save revision';}
}
async function saveRev(id,key){
  const ta=$(id),btn=$('btn_'+id),stEl=$(stId(key));
  const jid=SELECTED;
  if(!ta||!btn||!jid) return;
  btn.disabled=true;
  btn.textContent='Saving';
  try{
    const r=await fetch('/api/revision/'+encodeURIComponent(jid),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({key:key,value:ta.value})});
    const j=await r.json().catch(()=>({}));
    if(!r.ok||!j.ok){
      if(stEl){stEl.textContent='Not saved: '+(j.error||('HTTP '+r.status));stEl.className='status-live tone-bad';}
      btn.disabled=false;btn.textContent='Save revision';
      return;
    }
    ta.dataset.eff=ta.value;
    if(stEl){stEl.textContent='Saved. The revision sidecar holds it.';stEl.className='status-live tone-ok';}
    btn.textContent='Saved';
    setTimeout(()=>{btn.textContent='Unchanged';btn.disabled=true;},1200);
  }catch(e){
    if(stEl){stEl.textContent='Not saved: '+e.message;stEl.className='status-live tone-bad';}
    btn.disabled=false;btn.textContent='Save revision';
  }
}
function copyVal(btn,taId){
  const ta=$(taId);
  const v=ta?(ta.dataset.eff||ta.value):'';
  const label=btn.textContent;
  if(!navigator.clipboard){btn.textContent='Clipboard blocked';setTimeout(()=>{btn.textContent=label;},2500);return;}
  navigator.clipboard.writeText(v).then(()=>{btn.textContent='Copied';setTimeout(()=>{btn.textContent=label;},2500);})
    .catch(()=>{btn.textContent='Clipboard blocked';setTimeout(()=>{btn.textContent=label;},2500);});
}
async function renderEvents(){
  try{
    const ev=await fetchJson('/api/events?limit=50');
    $('events').innerHTML=ev.length?ev.map(e=>'<div class="ev"><span class="ev-ts">'+esc(e.ts)+'</span> <span class="ev-kind">'+esc(e.kind)+'</span> '+esc(e.job_id||'')+' '+esc(e.detail||'')+'</div>').join(''):emptyState('No event recorded yet.');
  }catch(e){
    $('events').innerHTML=errorState('Could not load events: '+e.message,'renderEvents()');
  }
}
async function load(){
  $('refreshtxt').textContent='refreshing';
  try{
    const apps=await fetchJson('/api/applications');
    const sig=JSON.stringify(apps);
    if(sig!==SIG){SIG=sig;APPS=apps;renderKPIs(APPS);renderApps();}
    $('lastupd').textContent='Updated '+new Date().toLocaleTimeString();
    $('refreshtxt').textContent='auto, every 5 seconds';
  }catch(e){
    $('refreshtxt').textContent='last refresh failed';
    if(!APPS.length) $('appsWrap').innerHTML=errorState('Could not load applications: '+e.message,'load()');
  }
  renderEvents();
}
async function loadFF(){
  const jid=SELECTED,out=$('ffout'),st=$('ffstatus');
  if(!jid){st.textContent='Select an application first.';return;}
  st.innerHTML='<span class="spinner" role="status" aria-label="Reading"></span>Reading the live form, up to 20 seconds.';
  out.innerHTML=appsSkeleton(4);
  try{
    const r=await fetchJson('/api/form-fields/'+encodeURIComponent(jid));
    if(r.error){st.textContent='Read failed: '+r.error;out.innerHTML='';return;}
    st.textContent=(r.fields||[]).length+' fields read.';
    out.innerHTML='<table><thead><tr><th>Label</th><th>Name</th><th>Type</th><th>Required</th><th>Current value</th><th>Options</th></tr></thead><tbody>'
      +(r.fields||[]).map(f=>'<tr><td>'+esc(f.label)+'</td><td class="mono">'+esc(f.name)+'</td><td>'+esc(f.type)+'</td><td>'+(f.required?'required':'')+'</td><td>'+esc(f.current_value||'')+'</td><td>'+esc((f.options||[]).join(', '))+'</td></tr>').join('')
      +'</tbody></table>';
  }catch(e){
    st.textContent='Read failed.';
    out.innerHTML=errorState('Could not read the live form: '+e.message,'loadFF()');
  }
}
function syncURL(){
  const p=new URLSearchParams();
  if(QUERY) p.set('q',QUERY);
  if(STATUS_FILTER!=='all') p.set('status',STATUS_FILTER);
  if(SORT!=='newest') p.set('sort',SORT);
  if(PAGE_N>1) p.set('page',String(PAGE_N));
  if(PAGE_SIZE!==50) p.set('size',String(PAGE_SIZE));
  if(SELECTED) p.set('sel',SELECTED);
  const qs=p.toString();
  history.replaceState(null,'',location.pathname+(qs?'?'+qs:''));
}
function restoreURL(){
  const p=new URLSearchParams(location.search);
  QUERY=(p.get('q')||'').trim().toLowerCase();
  STATUS_FILTER=p.get('status')||'all';
  SORT=['newest','company','status','confidence'].includes(p.get('sort'))?p.get('sort'):'newest';
  const size=parseInt(p.get('size')||'',10);
  PAGE_SIZE=[25,50,100].includes(size)?size:50;
  PAGE_N=Math.max(1,parseInt(p.get('page')||'1',10)||1);
  $('qsearch').value=p.get('q')||'';
  $('sortsel').value=SORT;
  $('pagesel').value=String(PAGE_SIZE);
  return p.get('sel')||'';
}
$('appsWrap').addEventListener('click',ev=>{
  const tr=ev.target.closest('tr[data-job]');
  if(!tr||!tr.dataset.job) return;
  selectApp(tr.dataset.job,tr.querySelector('.row-title'));
});
$('statuschips').addEventListener('click',ev=>{const b=ev.target.closest('.chip');if(b) setFilter(b.dataset.s);});
$('qsearch').addEventListener('input',e=>{QUERY=e.target.value.trim().toLowerCase();PAGE_N=1;renderApps();syncURL();});
$('sortsel').addEventListener('change',e=>{SORT=e.target.value;PAGE_N=1;renderApps();syncURL();});
$('pagesel').addEventListener('change',e=>{PAGE_SIZE=parseInt(e.target.value,10)||50;PAGE_N=1;renderApps();syncURL();});
$('ffbtn').addEventListener('click',loadFF);
document.addEventListener('keydown',e=>{if(e.key==='Escape'&&!$('drawer').hidden)closeDrawer();});
window.addEventListener('popstate',()=>{
  const sel=restoreURL();
  if(sel&&sel!==SELECTED) selectApp(sel);
  else if(!sel&&!$('drawer').hidden) closeDrawer(false);
  else renderApps();
});
const INITIAL_SEL=restoreURL();
$('appsWrap').innerHTML=appsSkeleton(8);
load().then(()=>{if(INITIAL_SEL&&APPS.some(a=>a.job_id===INITIAL_SEL)) selectApp(INITIAL_SEL);else syncURL();});
setInterval(load,5000);
</script></body></html>"""

if __name__ == "__main__":
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.ThreadingTCPServer((HOST, PORT), H) as httpd:
        print(f"listening on http://{HOST}:{PORT}", flush=True)
        httpd.serve_forever()
