# Captcha (Cloudflare Turnstile) — job-autopilot

## Purpose

`ja/captcha.py` wraps the vendored native solver at
`~/.job-autopilot/vendor/turnstile_solver/` so form submissions can pass a
legitimate Turnstile check on the candidate's own application for their own
account. The solver uses a real Playwright/Chromium browser plus a JS hook
injected into `window.turnstile`; it does not use a paid third-party API.

Vendor (unzipped, original zip untouched):
`~/.job-autopilot/vendor/turnstile_solver/`

## Install prerequisites (PARTIALLY EXECUTED — see verification status)

Core Playwright engine already works (no install needed):

- `playwright` 1.x importable at
  `~/.local/lib/python3.14/site-packages/playwright/__init__.py`
- Chromium `chromium-1243` (version 145.0.7632.6) present under
  `~/.cache/ms-playwright` and launches headless with `--no-sandbox`.
- `fastapi`, `uvicorn`, `curl_cffi`, `loguru`, `requests`, `aiohttp` all
  importable.

Camoufox (CLI + `--browser` sitekey scan) — installed as a Python package
but its browser fetch fails:

```bash
python3 -m pip install --user 'camoufox[geoip]>=0.4.0'   # SUCCEEDED (camoufox 0.5.9)
python3 -m camoufox fetch                                 # FAILED — timed out at 300s, 0 bytes downloaded
```

Remaining install steps for a later human-approved run (slow network):

```bash
cd ~/.job-autopilot/vendor/turnstile_solver
python3 -m camoufox fetch                          # retry; needs ~160 MB, timed out twice at 300s
```

`requirements.txt` (exact):

```
camoufox[geoip]>=0.4.0
fastapi>=0.110.0
uvicorn>=0.28.0
curl_cffi>=0.7.0
loguru>=0.7.2
playwright>=1.40.0
```

Other requirements: Python 3.9+, Linux/macOS/Windows. `config.json` keys:
`host` (127.0.0.1), `port` (8000), `headless` (true), `thread` (3),
`page_count` (1), `proxy_support` (false), `proxy_file` (proxies.txt),
`cleanup_interval_minutes` (5), `debug` (false).

## Invocation modes

### 1. Standalone CLI

```bash
cd ~/.job-autopilot/vendor/turnstile_solver
python3 solve_turnstile.py https://target.com/login 0x4AAAAAAAx...
python3 solve_turnstile.py https://target.com/login          # auto-detect sitekey (needs camoufox)
TURNSTILE_PROXY="socks5://user:pass@host:port" \
  python3 solve_turnstile.py https://target.com/login 0x4AAAAAAAx...
```

Prints `TOKEN=...` to stdout and writes the token to
`/tmp/turnstile_token.txt` (or `$TURNSTILE_OUT`). Exit 0 on success.
**Note:** this CLI imports `camoufox` at runtime, so it is NOT usable
until `camoufox fetch` succeeds. Use the Python engine instead.

### 2. Python engine (verified working)

```python
import sys, os
sys.path.insert(0, os.path.expanduser("~/.job-autopilot/vendor/turnstile_solver"))
from ts_engine import TurnstileEngine

engine = TurnstileEngine(headless=True, proxy="socks5://user:pass@ip:port")  # or None
result = engine.solve(
    url="https://target.com/login",
    sitekey="0x4AAAAAAAx...",
    action=None,
    timeout=45.0,
    click_submit=False,
)
if result.get("ok"):
    token = result["token"]
    print(f"ok, len={result['len']}")
else:
    print("fail:", result.get("error"))
```

Engine uses Playwright Chromium (`pw.chromium.launch`, `--no-sandbox`,
`--disable-blink-features=AutomationControlled`, etc.), injects `PATCH_JS`
via `add_init_script`, polls for up to `timeout` (default 45s), auto-clicks
the widget up to 4 times, and returns `ok/token/len/ms/state/clicks`.
**Note:** README says `result["value"]`; actual code returns `result["token"]`.

### 3. FastAPI service

```bash
cd ~/.job-autopilot/vendor/turnstile_solver
python3 eni_ts_api.py                          # 127.0.0.1:8123
ENI_TS_HOST=127.0.0.1 ENI_TS_PORT=8123 python3 eni_ts_api.py
```

```bash
curl -s "http://127.0.0.1:8123/sitekey?url=https://target.com/login"
curl -s "http://127.0.0.1:8123/solve?url=https://target.com/login&sitekey=0x4AAAAAAAx..."
```

Binds to localhost only — do not expose on 0.0.0.0. Python client:
`client.TurnstileClient(base_url="http://127.0.0.1:8123")`.

## Proxy requirements

- `TURNSTILE_PROXY` — used by `solve_turnstile.py` (CLI).
- `ENI_TS_PROXY` — default for `ts_engine.py` and `eni_ts_api.py`.
- Formats: `socks5://user:pass@ip:port`, `socks5://ip:port`,
  `http://user:pass@ip:port`, `http://ip:port`.
- Per-call override: `TurnstileEngine.solve(..., proxy=...)`.
- The toolkit README warns that datacenter IPs get low trust from Cloudflare
  Turnstile → interactive challenge or loop. Residential or WARP proxy may be
  required, i.e. real cost. This is a candidate decision (PLAN.md §12 (4)).

## Token handling

- CLI mode writes the token to `/tmp/turnstile_token.txt` or `$TURNSTILE_OUT`
  (not yet verified — CLI needs camoufox).
- Engine/API return the token in memory (result dict / JSON response); no
  disk write by those modes. On failure the engine writes a screenshot to
  `/tmp/eni_ts_fail.png` (no token).
- Tokens are single-use and short-lived: fetch immediately before submitting
  the form, do not cache across runs, do not commit tokens to the repo, and
  do not log full tokens.

## Captcha policy (verbatim from PLAN.md §6)

> Captcha policy: this is a legitimate bot-check on the candidate's own
> application for their own account — attempt it; never create accounts,
> never solve a login wall for an account that isn't the candidate's, never
> bulk-solve, max 3 attempts per form then hand the page to the candidate.

## Troubleshooting

- Challenge loop / token never appears: low IP trust — switch to a
  residential/WARP proxy via `TURNSTILE_PROXY`/`ENI_TS_PROXY`.
- Browser not found: run `python3 -m playwright install chromium` (already
  present; engine uses Playwright's own Chromium, not CloakBrowser or
  PinchTab).
- API port busy: change `ENI_TS_PORT` (default 8123) or stop the other
  process; service binds 127.0.0.1 only.
- Sitekey not found: use `get_sitekey.py <url>` (curl-based) or
  `--browser` (requires camoufox, currently missing).
- Token too short / rejected: raise `ENI_TS_TOKEN_MIN` (default 20) to filter
  dummy tokens; default is loose for pipeline testing.
- Camoufox fetch hangs at 0 bytes: network too slow or blocked; retry with
  longer timeout or a different network.

## Verification status (2026-11-03)

**Backend exercised:** Playwright Chromium (engine path, no camoufox import).

**Exact command:**

```bash
cd ~/.job-autopilot/vendor/turnstile_solver
python3 -c "
import sys, time, json
sys.path.insert(0, '.')
from ts_engine import TurnstileEngine
eng = TurnstileEngine(headless=True)
r = eng.solve(url='https://demo.turnstile.workers.dev/',
              sitekey='1x00000000000000000000AA', timeout=60.0, keep_open=0)
print(json.dumps({k:v for k,v in r.items() if k not in ('token','state','log','click_boxes','final_state')}, indent=2))
print('TOKEN_LEN:', len(r.get('token') or ''))
eng.close()
"
```

**Result:** `ok: true`, token length 21, prefix `XXXX.DUMMY.TOKEN.XXX...`,
elapsed 4.6s, 1 click. This is Cloudflare's documented test sitekey
(`1x00000000000000000000AA`) which returns a dummy token — confirms the
engine pipeline works end-to-end on the Playwright/Chromium backend.

A prior attempt on `https://nopecha.com/demo/turnstile` with no explicit
sitekey failed (`ok: false`, `no-widget-and-no-sitekey`) because that page
did not expose a sitekey in its HTML and the `--browser` scan requires
camoufox.

**What remains unverified:**

- Real (non-dummy) token from a production Turnstile widget with a real
  sitekey and a trusted IP/proxy. Needs a residential/WARP proxy decision
  (PLAN.md §12 (4)).
- CLI mode (`solve_turnstile.py`) and `--browser` sitekey scan — both
  require camoufox, whose fetch times out at 300s with 0 bytes downloaded.
- FastAPI service (`eni_ts_api.py`) — imports work; not smoke-tested as a
  running service.