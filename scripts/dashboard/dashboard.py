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
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>job-autopilot dashboard</title>
<style>
:root{--bg:#f6f7f9;--card:#ffffff;--border:#e3e6ea;--text:#1f2328;--muted:#57606a;--accent:#2563eb;--ok:#1a7f37;--warn:#b45309;--bad:#cf222e;--shadow:0 1px 3px rgba(0,0,0,.08),0 1px 2px rgba(0,0,0,.06);--success:var(--ok);--warning:var(--warn);--error:var(--bad)}
*{box-sizing:border-box}
body{margin:0;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;font-size:16px;line-height:1.5;color:var(--text);background:var(--bg)}
.topbar{position:sticky;top:0;z-index:50;background:var(--card);border-bottom:1px solid var(--border);padding:12px 16px;display:flex;align-items:center;gap:12px;flex-wrap:wrap}
.topbar h1{margin:0;font-size:18px;font-weight:700}
.topbar .meta{margin-left:auto;display:flex;align-items:center;gap:14px;font-size:13px;color:var(--muted)}
.refresh-dot{width:8px;height:8px;border-radius:50%;background:#9ca3af;display:inline-block}
.refresh-dot.live{background:var(--ok);animation:pulse 1.2s infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.35}}
@media(prefers-reduced-motion:reduce){.refresh-dot.live{animation:none}}
.wrap{max-width:1280px;margin:0 auto;padding:16px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px;margin:16px 0}
.kpi{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:16px;box-shadow:var(--shadow)}
.kpi .label{font-size:13px;font-weight:600;color:var(--muted);margin:0}
.kpi .num{font-size:34px;font-weight:700;margin:4px 0 2px}
.kpi .sub{font-size:12px;color:var(--muted)}
.section{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:16px;margin:16px 0;box-shadow:var(--shadow)}
.section h2{margin:0 0 12px;font-size:17px}
.toolbar{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:12px}
.toolbar input,.toolbar select{padding:8px 10px;border:1px solid var(--border);border-radius:8px;font-size:14px;background:#fff;max-width:100%}
#qajob{max-width:min(520px,100%)}
.toolbar input{flex:1;min-width:180px}
.chips{display:flex;gap:6px;flex-wrap:wrap}
.chip{padding:4px 10px;border-radius:999px;border:1px solid var(--border);background:#fff;font-size:12px;cursor:pointer;color:var(--text)}
.chip.active{background:var(--accent);color:#fff;border-color:var(--accent)}
.chip .n{font-weight:700;margin-left:4px}
table{width:100%;border-collapse:collapse;font-size:14px;table-layout:fixed}
th,td{padding:10px 8px;text-align:left;vertical-align:top;border-bottom:1px solid var(--border)}
th{background:#f1f3f5;font-weight:600;position:sticky;top:62px;z-index:5}
tbody tr:nth-child(even){background:#fafbfc}
tbody tr{cursor:pointer}
tbody tr:hover{background:#eef3ff}
tbody tr.selected{background:#e0eaff}
.mono{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:12px}
.status-pill{display:inline-block;padding:2px 8px;border-radius:999px;color:#fff;font-size:11px;font-weight:600}
.s-submitted{background:#2e7d32}.s-prefilled{background:#1565c0}.s-drafted{background:#6a1b9a}
.s-shortlisted{background:#ef6c00}.s-discovered{background:#757575}.s-gated{background:#9e9e9e}
.s-withdrawn{background:#c62828}.s-submitted_auto{background:#00897b}.s-submitted_human{background:#43a047}
.legend{display:flex;gap:8px;flex-wrap:wrap;margin-top:8px;font-size:11px;color:var(--muted)}
.legend span{display:inline-flex;align-items:center;gap:4px}
.legend .dot{width:8px;height:8px;border-radius:50%}
.qa-header{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:10px}
.qa-header h3{margin:0;font-size:16px}
.qa-meta{font-size:12px;color:var(--muted)}
.qcard{border:1px solid var(--border);border-radius:10px;padding:12px;margin:10px 0;background:#fff}
.qcard.needs{border-left:4px solid var(--bad)}
.qcard .label{font-weight:600;font-size:14px;margin:0 0 4px}
.qcard .key{font-size:11px;color:var(--muted);font-family:ui-monospace,monospace}
.qcard .val{background:#f6f8fa;border:1px solid var(--border);border-radius:6px;padding:6px 8px;font-size:13px;margin:6px 0;word-break:break-word}
.qcard textarea{width:100%;min-height:56px;padding:8px;border:1px solid var(--border);border-radius:6px;font-size:13px;font-family:inherit}
.qcard .actions{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-top:8px}
.btn{padding:6px 12px;border:1px solid var(--border);border-radius:8px;background:#fff;cursor:pointer;font-size:13px}
.btn.primary{background:var(--accent);color:#fff;border-color:var(--accent)}
.btn:disabled{opacity:.5;cursor:not-allowed}
.btn:focus-visible,.chip:focus-visible,input:focus-visible,select:focus-visible,textarea:focus-visible,button:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.badge{display:inline-block;padding:2px 8px;border-radius:999px;font-size:11px;font-weight:600}
.badge.edited{background:#fef3c7;color:#92400e;border:1px solid #fcd34d}
.badge.saved{color:var(--ok);font-size:12px}
.hint{font-size:12px;color:var(--muted);margin:6px 0}
.status-live{font-size:12px;min-height:16px}
.empty{padding:24px;text-align:center;color:var(--muted)}
.empty svg{opacity:.5;margin-bottom:8px}
.error-box{padding:16px;border:1px solid #fecaca;background:#fef2f2;border-radius:10px;color:#991b1b}
.spinner{display:inline-block;width:16px;height:16px;border:2px solid #d1d5db;border-top-color:var(--accent);border-radius:50%;animation:spin 1s linear infinite;vertical-align:middle;margin-right:6px}
@keyframes spin{to{transform:rotate(360deg)}}
@media(prefers-reduced-motion:reduce){.spinner{animation:none}}
.skeleton{background:linear-gradient(90deg,#eee 25%,#f5f5f5 50%,#eee 75%);background-size:200% 100%;animation:shimmer 1.4s infinite;border-radius:6px;height:14px;margin:6px 0}
@keyframes shimmer{0%{background-position:200% 0}100%{background-position:-200% 0}}
@media(prefers-reduced-motion:reduce){.skeleton{animation:none}}
#events div{overflow-wrap:anywhere}
.mono,.hint,.qa-meta,.val,.error-box,td,th{overflow-wrap:anywhere}
.cards-stack{display:none}
@media(max-width:700px){
  table.apps-table thead{display:none}
  table.apps-table,table.apps-table tbody,table.apps-table tr,table.apps-table td{display:block;width:100%}
  table.apps-table tr{margin-bottom:10px;border:1px solid var(--border);border-radius:8px;padding:8px;background:#fff}
  table.apps-table td{border:none;padding:4px 0}
  table.apps-table td::before{content:attr(data-label);font-weight:600;color:var(--muted);font-size:11px;display:block}
  body{font-size:16px}
}
.sr-only{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}
.conf-pill{display:inline-flex;align-items:center;gap:6px;flex-wrap:wrap;max-width:100%;padding:2px 8px;border:1px solid var(--border);border-radius:999px;font-size:11px;font-weight:600;line-height:1.2}
.conf-bar{width:34px;height:4px;background:var(--border);border-radius:2px;overflow:hidden}
.conf-bar>span{display:block;height:100%;background:currentColor}
.conf-unknown{color:var(--muted)}
.conf-summary{border:1px solid var(--border);border-radius:10px;padding:12px;margin:0 0 12px;background:#fafbfc}
.conf-summary .pct{font-size:24px;font-weight:700}
.conf-ok{color:var(--success)}
.conf-warn{color:var(--warning)}
.conf-bad{color:var(--error)}
.conf-head{display:flex;align-items:center;gap:8px}
.conf-summary h4{margin:8px 0 4px;font-size:13px}
.conf-missing{margin:0;padding-left:18px;font-size:13px;overflow-wrap:anywhere}
.pager .btn{padding:4px 10px}
.pager{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-top:10px;font-size:13px;color:var(--muted)}
</style></head><body>
<div class="topbar">
  <h1>job-autopilot dashboard</h1>
  <div class="meta">
    <span id="lastupd">never</span>
    <span><span class="refresh-dot" id="rdot"></span> <span id="refreshtxt">idle</span></span>
  </div>
</div>
<div class="wrap">
  <div class="kpis" id="kpis"></div>
  <div class="section">
    <h2>Applications</h2>
    <div class="toolbar">
      <label class="sr-only" for="qsearch">Search applications</label>
      <input id="qsearch" type="search" placeholder="Search company or role…">
      <label class="sr-only" for="sortsel">Sort</label>
      <select id="sortsel"><option value="newest">Newest first</option><option value="company">Company</option><option value="status">Status</option><option value="confidence">Confidence</option></select>
      <label class="sr-only" for="pagesel">Rows per page</label>
      <select id="pagesel"><option>25</option><option selected>50</option><option>100</option></select>
    </div>
    <div class="chips" id="statuschips"></div>
    <div class="legend" id="legend"></div>
    <div id="appsWrap"></div>
  </div>
  <div class="section">
    <h2>Q&amp;A / Revise</h2>
    <div class="qa-tools">
      <label class="sr-only" for="qajob">Choose application</label>
      <select id="qajob"><option value="">— pick an application —</option></select>
      <button class="btn" onclick="reloadQA()">Reload</button>
      <span class="qa-meta" id="qameta"></span>
    </div>
    <div id="qaout" aria-live="polite"><div class="empty"><svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="12" cy="12" r="10"/><path d="M8 12h8M12 8v8"/></svg><div>Pick an application above to view and revise its Q&A.</div></div></div>
  </div>
  <div class="section">
    <h2>Live form fields</h2>
    <div class="toolbar">
      <button class="btn" onclick="loadFF()">Fetch live fields</button>
      <span id="ffstatus" class="status-live" aria-live="polite"></span>
    </div>
    <div id="ffout"></div>
  </div>
  <div class="section">
    <h2>Live events</h2>
    <div id="events"></div>
  </div>
</div>
<script>
let APPS=[]; let SELECTED=''; let STATUS_FILTER='all'; let LAST_LOAD=0; let PAGE_N=1; let PAGE_SIZE=50;
const esc=s=>(s==null?'':''+s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
function emptyState(msg){return '<div class="empty"><svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="12" cy="12" r="10"/><path d="M8 12h8M12 8v8"/></svg><div>'+esc(msg)+'</div></div>';}
function errorState(msg,cb){return '<div class="error-box"><div>'+esc(msg)+'</div><button class="btn" style="margin-top:8px" onclick="'+cb+'">Retry</button></div>';}
async function fetchJson(url){const r=await fetch(url);if(!r.ok){let m='Request failed ('+r.status+')';try{const j=await r.json();if(j&&j.error)m=j.error;}catch(e){}throw new Error(m);}return r.json();}
function fmtDate(v){return v==null?'':String(v);}
function renderKPIs(apps){
  const counts={};apps.forEach(a=>{counts[a.status]=(counts[a.status]||0)+1;});
  const submitted=counts.submitted||0;
  const ready=(counts.prefilled||0)+(counts.shortlisted||0);
  const emailed=apps.filter(a=>a.channel==='email').length;
  const companies=new Set(apps.map(a=>a.company).filter(Boolean)).size;
  const confRows=apps.filter(a=>confPct(a.confidence)!=null);
  const avg=confRows.length? Math.round(confRows.reduce((s,a)=>s+confPct(a.confidence),0)/confRows.length) : null;
  const avgNum=avg==null ? '—' : (avg+'% · '+confRows.length+' of '+apps.length);
  const defs=[
    ['Submitted',submitted,"Confirmed by the company's confirmation page."],
    ['Ready to submit',ready,'Prefilled or shortlisted, waiting for final submit.'],
    ['Emailed',emailed,'Applications sent by email channel.'],
    ['Companies total',companies,'Distinct companies in the tracker.'],
    ['Avg confidence',avgNum,'Average over rows with confidence data.']
  ];
  document.getElementById('kpis').innerHTML=defs.map(d=>'<div class="kpi"><p class="label">'+esc(d[0])+'</p><div class="num">'+esc(d[1])+'</div><div class="sub">'+esc(d[2])+'</div></div>').join('');
}
function confPct(c){
  if(!c||c.pct==null||c.pct==='') return null;
  const n=Math.round(Number(c.pct));
  return isNaN(n)?null:Math.max(0,Math.min(100,n));
}
function confCls(p){return p>=80?'conf-ok':(p>=50?'conf-warn':'conf-bad');}
function confText(c){
  const missing=(c&&c.missing?c.missing:[]).filter(m=>m!=null&&m!=='');
  return 'method: '+(esc(c&&c.method||'unknown'))+' · filled '+(c&&c.filled!=null?esc(String(c.filled)):'?')+'/'+(c&&c.total!=null?esc(String(c.total)):'?')+
    (missing.length?' · missing: '+esc(missing.join(', ')):'');
}
function confPill(c){
  const p=confPct(c);
  if(p==null) return '<span class="conf-pill conf-unknown" title="no confidence data recorded" aria-label="Confidence: no confidence data recorded">—</span>';
  const t=confText(c);
  return '<span class="conf-pill '+confCls(p)+'" title="'+t+'" aria-label="Confidence '+p+' percent; '+t+'">'+p+'%<span class="conf-bar" aria-hidden="true"><span style="width:'+p+'%"></span></span></span>';
}
function confSummary(c){
  const p=confPct(c);
  if(p==null) return '<div class="conf-summary conf-unknown"><div class="hint">No confidence data recorded for this application.</div></div>';
  const missing=(c.missing||[]).filter(m=>m!=null&&m!=='');
  let h='<div class="conf-summary"><div class="conf-head '+confCls(p)+'"><span class="pct">'+p+'%</span><span class="conf-bar" aria-hidden="true"><span style="width:'+p+'%"></span></span></div>';
  h+='<div class="hint">'+confText(c)+(c.channel?' · channel: '+esc(c.channel):'')+'</div>';
  if(missing.length){
    h+='<h4>Not filled from profile — needs your answer</h4><ul class="conf-missing">'+
      missing.slice(0,8).map(m=>'<li>'+esc(m)+'</li>').join('')+'</ul>';
    if(missing.length>8) h+='<div class="hint">and '+(missing.length-8)+' more</div>';
  }
  return h+'</div>';
}
async function confSummaryFor(jid){
  let c=null;
  try{
    const r=await fetch('/api/confidence/'+encodeURIComponent(jid));
    if(r.ok){const j=await r.json();c=j.confidence;}
  }catch(e){}
  return confSummary(c);
}
function statusCounts(apps){const c={};apps.forEach(a=>{c[a.status]=(c[a.status]||0)+1;});return c;}
function renderChips(){
  const c=statusCounts(APPS);
  const all=APPS.length;
  let html='<button class="chip'+(STATUS_FILTER==='all'?' active':'')+'" data-s="all" onclick="setFilter(\'all\')">All <span class="n">'+all+'</span></button>';
  Object.keys(c).sort().forEach(s=>{html+='<button class="chip'+(STATUS_FILTER===s?' active':'')+'" data-s="'+esc(s)+'" onclick="setFilter(\''+esc(s).replace(/'/g,'')+'\')">'+esc(s)+' <span class="n">'+c[s]+'</span></button>';});
  document.getElementById('statuschips').innerHTML=html;
  // legend
  const legendCols=['submitted','prefilled','drafted','shortlisted','discovered','gated','withdrawn'];
  document.getElementById('legend').innerHTML=legendCols.map(s=>'<span><span class="dot" style="background:'+colorFor(s)+'"></span>'+esc(s)+'</span>').join('');
}
function colorFor(s){
  const map={submitted:'#2e7d32',prefilled:'#1565c0',drafted:'#6a1b9a',shortlisted:'#ef6c00',discovered:'#757575',gated:'#9e9e9e',withdrawn:'#c62828',submitted_auto:'#00897b',submitted_human:'#43a047'};
  return map[s]||'#757575';
}
function setFilter(s){STATUS_FILTER=s;renderApps();}
function sortApps(list){
  const mode=document.getElementById('sortsel').value;
  const arr=[...list];
  if(mode==='company') arr.sort((a,b)=>String(a.company||'').localeCompare(String(b.company||'')));
  else if(mode==='status') arr.sort((a,b)=>String(a.status||'').localeCompare(String(b.status||'')));
  else if(mode==='confidence') arr.sort((a,b)=>{const x=confPct(a.confidence),y=confPct(b.confidence);
    if(x==null&&y==null) return 0; if(x==null) return 1; if(y==null) return -1; return y-x;});
  return arr;
}
function renderApps(){
  renderChips();
  const qv=document.getElementById('qsearch').value.toLowerCase();
  let list=APPS.filter(a=>{
    if(STATUS_FILTER!=='all' && a.status!==STATUS_FILTER) return false;
    if(qv && !(String(a.company||'').toLowerCase().includes(qv)||String(a.role||'').toLowerCase().includes(qv))) return false;
    return true;
  });
  list=sortApps(list);
  const wrap=document.getElementById('appsWrap');
  if(!list.length){wrap.innerHTML=emptyState('No applications match your filters.');return;}
  const total=list.length;
  const pages=Math.max(1,Math.ceil(total/PAGE_SIZE));
  if(PAGE_N>pages) PAGE_N=pages;
  if(PAGE_N<1) PAGE_N=1;
  const start=(PAGE_N-1)*PAGE_SIZE, end=Math.min(start+PAGE_SIZE,total);
  let html='<table class="apps-table"><thead><tr><th>ID</th><th>Company</th><th>Role</th><th>Channel</th><th>Status</th><th>Created</th><th>Submitted</th><th data-sort="confidence">Confidence</th><th>Gate</th></tr></thead><tbody>';
  list.slice(start,end).forEach(a=>{
    const sel=a.job_id===SELECTED?'selected':'';
    html+='<tr class="'+sel+'" data-job="'+esc(a.job_id||'')+'" onclick="selectApp(\''+esc(a.job_id||'').replace(/'/g,'')+'\')">'+
      '<td class="mono" data-label="ID">'+esc(a.application_id)+'</td>'+
      '<td data-label="Company">'+esc(a.company||'')+'</td>'+
      '<td data-label="Role">'+esc(a.role||'')+'</td>'+
      '<td data-label="Channel">'+esc(a.channel||'')+'</td>'+
      '<td data-label="Status"><span class="status-pill s-'+esc(a.status)+'">'+esc(a.status)+'</span></td>'+
      '<td class="mono" data-label="Created">'+fmtDate(a.created_at)+'</td>'+
      '<td class="mono" data-label="Submitted">'+fmtDate(a.submitted_at)+'</td>'+
      '<td data-label="Confidence">'+confPill(a.confidence)+'</td>'+
      '<td data-label="Gate">'+(a.gate_score==null?'':esc(a.gate_score))+'</td></tr>';
  });
  html+='</tbody></table>';
  html+='<div class="pager"><button class="btn" onclick="goPage('+(PAGE_N-1)+')"'+(PAGE_N<=1?' disabled':'')+'>Prev</button>'+
    '<span>Page '+PAGE_N+' of '+pages+' — showing '+(total? start+1:0)+'–'+end+' of '+total+'</span>'+
    '<button class="btn" onclick="goPage('+(PAGE_N+1)+')"'+(PAGE_N>=pages?' disabled':'')+'>Next</button></div>';
  wrap.innerHTML=html;
}
function goPage(n){PAGE_N=n;renderApps();}
function selectApp(jid){
  if(!jid) return;
  SELECTED=jid;
  const sel=document.getElementById('qajob');
  sel.value=jid;
  renderApps();
  loadQA(jid);
}
async function load(){
  const d=document.getElementById('rdot'); d.classList.add('live');
  document.getElementById('refreshtxt').textContent='refreshing…';
  try{
    APPS=await fetchJson('/api/applications');
    renderKPIs(APPS);
    // populate dropdown
    const sel=document.getElementById('qajob');
    const cur=sel.value;
    sel.innerHTML='<option value="">— pick an application —</option>'+APPS.filter(a=>a.job_id).map(a=>
      '<option value="'+esc(a.job_id)+'">'+esc(a.company||a.job_id)+' — '+esc(a.role||'')+' ('+esc(a.status)+')</option>').join('');
    sel.value=cur||SELECTED;
    renderApps();
    const ev=await fetchJson('/api/events?limit=50');
    document.getElementById('events').innerHTML=ev.map(e=>'<div class="mono" style="padding:2px 0">'+esc(e.ts)+' ['+esc(e.kind)+'] '+esc(e.job_id||'')+' '+esc(e.detail||'')+'</div>').join('')||emptyState('No events yet.');
    LAST_LOAD=Date.now();
    document.getElementById('lastupd').textContent='Updated '+new Date().toLocaleTimeString();
  }catch(e){
    document.getElementById('appsWrap').innerHTML=errorState('Could not load applications: '+e.message,'load()');
  }finally{
    d.classList.remove('live'); document.getElementById('refreshtxt').textContent='auto (5s)';
  }
}
async function loadQA(jid,force){
  const out=document.getElementById('qaout'),meta=document.getElementById('qameta');
  if(!jid){out.innerHTML=emptyState('Pick an application above to view and revise its Q&A.');meta.textContent='';return;}
  if(force) out.dataset.jid='';
  out.innerHTML='<div class="skeleton"></div><div class="skeleton"></div><div class="skeleton"></div>';
  meta.textContent='';
  try{
    const qa=await fetchJson('/api/qa/'+jid);
    meta.textContent=(qa.company||'')+' — '+(qa.role||'')+' ['+(qa.ats||'?')+']'+(qa.stopped?' (stopped: '+qa.stopped+')':'');
    const sum=await confSummaryFor(jid);
    const revs=qa.revision||{}, eff=qa.effective||{};
    const nh={}; (qa.needs_human||[]).forEach(n=>{if(n.field) nh[n.field]=n.reason;});
    const keys=[...new Set([...(qa.filled||[]).map(r=>r.key),...Object.keys(revs),...Object.keys(nh)])].filter(Boolean);
    if(!keys.length){out.innerHTML=sum+emptyState('No questions found for this application.');return;}
    out.innerHTML=sum+keys.map(k=>{
      const row=(qa.filled||[]).find(r=>r.key===k)||{};
      const isEdited=revs[k]!=null;
      const isNeeds=nh[k]!=null;
      const effVal=eff[k]==null?'':eff[k];
      const filledVal=row.value==null?'':row.value;
      const id='rev_'+btoa(unescape(encodeURIComponent(k))).replace(/=+$/,'');
      const stid='st_'+k.replace(/[^A-Za-z0-9]/g,'');
      let html='<div class="qcard'+(isNeeds?' needs':'')+'">';
      html+='<p class="label">'+esc(row.label||k)+'</p>';
      html+='<div class="key">'+esc(k)+'</div>';
      if(isEdited) html+='<span class="badge edited">edited</span> ';
      if(isNeeds) html+='<span class="badge" style="background:#fee2e2;color:#991b1b;border:1px solid #fecaca">needs human</span>';
      html+='<div class="val"><strong>Filled:</strong> '+esc(filledVal)+'</div>';
      if(isNeeds){
        const reason=nh[k];
        const short=reason.length>120?reason.slice(0,120)+'…':reason;
        html+='<details><summary style="cursor:pointer;color:var(--bad);font-size:12px">Reason</summary><div class="hint" style="color:var(--bad)">'+esc(reason)+'</div></details>';
        if(reason.length>120) html+='<div class="hint">'+esc(short)+'</div>';
      }
      html+='<label class="sr-only" for="'+id+'">Revision for '+esc(row.label||k)+'</label>';
      html+='<textarea id="'+id+'" data-key="'+esc(k)+'" oninput="syncSaveBtn(\''+id+'\')" aria-label="Revision">'+esc(effVal)+'</textarea>';
      html+='<div class="actions">';
      html+='<button class="btn primary" id="btn_'+id+'" onclick="saveRev(\''+id+'\',\''+esc(k).replace(/'/g,'')+'\')">Save revision</button>';
      html+='<button class="btn" onclick="copyVal(\''+esc(effVal).replace(/'/g,'')+'\')">Copy effective value</button>';
      html+='<span class="status-live" id="'+stid+'" aria-live="polite"></span>';
      html+='</div>';
      html+='<div class="hint">Effective value: <span class="mono">'+esc(effVal)+'</span></div>';
      html+='</div>';
      return html;
    }).join('');
    keys.forEach(k=>{
      const id='rev_'+btoa(unescape(encodeURIComponent(k))).replace(/=+$/,'');
      syncSaveBtn(id);
    });
  }catch(e){
    out.innerHTML=errorState('Could not load Q&A: '+e.message,'loadQA(\''+jid+'\',true)');
  }
}
function syncSaveBtn(id){
  const ta=document.getElementById(id);
  const btn=document.getElementById('btn_'+id);
  if(!ta||!btn) return;
  const orig=ta.dataset.orig==null?(ta.dataset.orig=ta.value,ta.value):ta.dataset.orig;
  // compare against effective value stored in hint? We'll store effective in data-eff
  const eff=ta.dataset.eff==null?(ta.dataset.eff=ta.value,ta.value):ta.dataset.eff;
  if(ta.value===eff){btn.disabled=true;btn.textContent='Unchanged';}
  else {btn.disabled=false;btn.textContent='Save revision';}
}
async function saveRev(id,key){
  const ta=document.getElementById(id);
  const btn=document.getElementById('btn_'+id);
  const stEl=document.getElementById('st_'+key.replace(/[^A-Za-z0-9]/g,''));
  if(!ta) return;
  btn.disabled=true; btn.textContent='Saving…';
  try{
    const r=await fetch('/api/revision/'+document.getElementById('qajob').value,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({key,value:ta.value})});
    const j=await r.json();
    if(!r.ok||!j.ok){ if(stEl){stEl.textContent='error: '+(j.error||r.status);stEl.style.color='var(--bad)';} btn.textContent='Save revision'; btn.disabled=false; return;}
    // update effective
    ta.dataset.eff=ta.value;
    if(stEl){stEl.textContent='Saved ✓';stEl.style.color='var(--ok)';}
    btn.textContent='Saved ✓';
    setTimeout(()=>{btn.textContent='Unchanged';btn.disabled=true;},1200);
    // refresh edited badge? reload lightly
  }catch(e){if(stEl){stEl.textContent='save failed: '+e;stEl.style.color='var(--bad)';}btn.textContent='Save revision';btn.disabled=false;}
}
function copyVal(v){
  if(navigator.clipboard){navigator.clipboard.writeText(v).then(()=>{}).catch(()=>{});}
}
async function loadFF(){
  const jid=document.getElementById('qajob').value;
  const out=document.getElementById('ffout'),st=document.getElementById('ffstatus');
  if(!jid){st.textContent='Pick an application first.';return;}
  st.innerHTML='<span class="spinner" role="status" aria-label="loading"></span> Reading the live form (up to 20 seconds)…';
  out.innerHTML='<div class="skeleton"></div><div class="skeleton"></div><div class="skeleton"></div>';
  try{
    const r=await fetchJson('/api/form-fields/'+jid);
    if(r.error){st.textContent='Error: '+r.error;out.innerHTML='';return;}
    st.textContent=(r.fields||[]).length+' fields loaded.';
    out.innerHTML='<table><thead><tr><th>Label</th><th>Name</th><th>Type</th><th>Required</th><th>Current value</th><th>Options</th></tr></thead><tbody>'+
      (r.fields||[]).map(f=>'<tr><td>'+esc(f.label)+'</td><td class="mono">'+esc(f.name)+'</td><td>'+esc(f.type)+'</td><td>'+(f.required?'*':'')+'</td><td>'+esc(f.current_value||'')+'</td><td>'+esc((f.options||[]).join(', '))+'</td></tr>').join('')+
      '</tbody></table>';
  }catch(e){st.textContent='Fetch failed: '+e.message;out.innerHTML=errorState('Could not fetch live form fields: '+e.message,'loadFF()');}
}
function reloadQA(){const v=document.getElementById('qajob').value;if(v) loadQA(v,true);}
document.getElementById('qsearch').addEventListener('input',()=>{PAGE_N=1;renderApps();});
document.getElementById('sortsel').addEventListener('change',()=>{PAGE_N=1;renderApps();});
document.getElementById('pagesel').addEventListener('change',e=>{PAGE_SIZE=parseInt(e.target.value,10)||50;PAGE_N=1;renderApps();});
document.getElementById('qajob').addEventListener('change',e=>{SELECTED=e.target.value;if(e.target.value) loadQA(e.target.value);});
load(); setInterval(load,5000);
</script></body></html>"""

if __name__ == "__main__":
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.ThreadingTCPServer((HOST, PORT), H) as httpd:
        print(f"listening on http://{HOST}:{PORT}", flush=True)
        httpd.serve_forever()
