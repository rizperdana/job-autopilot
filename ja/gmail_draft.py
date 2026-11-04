"""Gmail draft channel: build + create + verify drafts (never send).

Stdlib only. Drafts only — no send endpoints anywhere in this module.
"""
import argparse
import base64
import json
import mimetypes
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from email.message import EmailMessage
from pathlib import Path
TOKEN_STORE_PATH = Path.home() / ".mcp-z" / "tokens.json"
MCP_CONFIG_PATH = Path.home() / ".omp" / "agent" / "mcp.json"
ACCOUNT_EMAIL_DEFAULT = "candidate@example.com"
DRAFTS_URL = "https://gmail.googleapis.com/gmail/v1/users/me/drafts"
TOKEN_URL = "https://oauth2.googleapis.com/token"
class GmailDraftError(Exception):
    pass


class TokenError(GmailDraftError):
    pass


class ConfigError(GmailDraftError):
    pass


class OAuthRefreshError(GmailDraftError):
    pass


class DraftCreationError(GmailDraftError):
    pass


class DraftVerificationError(GmailDraftError):
    pass


def _load_token_entries(path=None):
    p = Path(path) if path is not None else TOKEN_STORE_PATH
    if not p.exists():
        raise TokenError("token store not found: " + str(p))
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise TokenError("cannot read token store " + str(p) + ": " + type(e).__name__)
    entries = None
    if isinstance(data, dict):
        if isinstance(data.get("cache"), list):
            entries = data["cache"]
        elif isinstance(data.get("entries"), list):
            entries = data["entries"]
    elif isinstance(data, list):
        entries = data
    if not isinstance(entries, list):
        raise TokenError("token store has no entry list: " + str(p))
    return entries, p

def get_account_email(path=None):
    entries, _ = _load_token_entries(path)
    suffix = ":mcp-gmail:token"
    for entry in entries:
        if not isinstance(entry, (list, tuple)) or len(entry) < 2:
            continue
        key = entry[0]
        if isinstance(key, str) and key.endswith(suffix):
            # key shape: <account>:mcp-gmail:token
            parts = key[:-len(suffix)].split(":", 1)
            if parts and "@" in parts[0]:
                return parts[0]

def get_refresh_token(path=None, account_email=None):
    entries, p = _load_token_entries(path)
    suffix = ":mcp-gmail:token"
    found = []
    for entry in entries:
        if not isinstance(entry, (list, tuple)) or len(entry) < 2:
            continue
        key, meta = entry[0], entry[1]
        if not isinstance(key, str) or not key.endswith(suffix):
            continue
        if account_email and account_email not in key:
            continue
        if not isinstance(meta, dict):
            continue
        raw = meta.get("value")
        obj = raw
        if isinstance(raw, str):
            try:
                obj = json.loads(raw)
            except ValueError:
                continue
        if not isinstance(obj, dict):
            continue
        if "value" in obj and isinstance(obj["value"], (dict, str)):
            inner = obj["value"]
            if isinstance(inner, str):
                try:
                    inner = json.loads(inner)
                except ValueError:
                    inner = None
            if isinstance(inner, dict):
                obj = inner
        rt = None
        if isinstance(obj, dict):
            rt = obj.get("refreshToken") or obj.get("refresh_token")
        if isinstance(rt, str) and rt:
            found.append(rt)
    if not found:
        raise TokenError("refresh token not found in token store: " + str(p))
    return found[0]


def get_client_credentials(path=None):
    p = Path(path) if path is not None else MCP_CONFIG_PATH
    if not p.exists():
        raise ConfigError("mcp config not found: " + str(p))
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise ConfigError("cannot read mcp config " + str(p) + ": " + type(e).__name__)
    env = {}
    try:
        env = data["mcpServers"]["gmail"]["env"]
    except (KeyError, TypeError):
        env = {}
    if not isinstance(env, dict):
        env = {}
    cid = env.get("GOOGLE_CLIENT_ID")
    csecret = env.get("GOOGLE_CLIENT_SECRET")
    if not isinstance(cid, str) or not cid:
        raise ConfigError("missing GOOGLE_CLIENT_ID in " + str(p) + " (mcpServers.gmail.env)")
    if not isinstance(csecret, str) or not csecret:
        raise ConfigError("missing GOOGLE_CLIENT_SECRET in " + str(p) + " (mcpServers.gmail.env)")
    return cid, csecret


def refresh_access_token(refresh_token, client_id, client_secret, timeout=30):
    params = urllib.parse.urlencode({
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    })
    req = urllib.request.Request(
        TOKEN_URL,
        data=params.encode("ascii"),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        code = None
        try:
            err = json.loads(e.read().decode("utf-8"))
            if isinstance(err, dict):
                code = err.get("error") or err.get("error_code")
        except (ValueError, OSError):
            code = None
        if not code:
            code = "HTTP " + str(e.code)
        raise OAuthRefreshError("OAuth token refresh failed: " + str(code))
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
        raise OAuthRefreshError("OAuth token refresh failed: " + type(e).__name__)
    if not isinstance(body, dict) or "access_token" not in body:
        raise OAuthRefreshError("OAuth token refresh failed: no access_token in response")
    return body["access_token"]


def sanitize_filename(name):
    base = os.path.basename(str(name))
    base = re.sub(r"[^A-Za-z0-9._-]", "_", base)
    if not base or base in (".", ".."):
        base = "attachment"
    return base


def _content_type(path):
    suffix = Path(path).suffix.lower()
    if suffix == ".pdf":
        return "application", "pdf"
    mt, _ = mimetypes.guess_type(str(path))
    if mt:
        parts = mt.split("/", 1)
        if len(parts) == 2:
            return parts[0], parts[1]
    return "application", "octet-stream"


def build_message(to, cc=None, subject="", body="", attachments=None):
    msg = EmailMessage()
    if isinstance(to, (list, tuple)):
        msg["To"] = ", ".join(str(x) for x in to)
    else:
        msg["To"] = str(to)
    if cc:
        if isinstance(cc, (list, tuple)):
            msg["Cc"] = ", ".join(str(x) for x in cc)
        else:
            msg["Cc"] = str(cc)
    msg["Subject"] = subject if subject is not None else ""
    msg.set_content(body if body is not None else "")
    for att in (attachments or []):
        p = Path(att).expanduser()
        data = p.read_bytes()
        maintype, subtype = _content_type(p)
        msg.add_attachment(
            data,
            maintype=maintype,
            subtype=subtype,
            filename=sanitize_filename(p.name),
        )
    return msg


def message_to_bytes(msg):
    return msg.as_bytes()


def b64url_encode(data):
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def b64url_decode(text):
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad)


def create_draft(access_token, raw_message, timeout=30):
    payload = json.dumps({"message": {"raw": raw_message}}).encode("utf-8")
    req = urllib.request.Request(
        DRAFTS_URL,
        data=payload,
        headers={
            "Authorization": "Bearer " + str(access_token),
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = None
        try:
            err = json.loads(e.read().decode("utf-8"))
            if isinstance(err, dict):
                detail = (err.get("error", {}) or {}).get("message") or err.get("error")
        except (ValueError, OSError):
            detail = None
        msg = "draft creation failed: HTTP " + str(e.code)
        if detail:
            msg += " " + str(detail)
        raise DraftCreationError(msg)
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
        raise DraftCreationError("draft creation failed: " + type(e).__name__)
    if not isinstance(body, dict) or "id" not in body:
        raise DraftCreationError("draft creation failed: no id in response")
    return str(body["id"])


def _collect_filenames(payload):
    out = []
    if not isinstance(payload, dict):
        return out
    fn = payload.get("filename")
    if isinstance(fn, str) and fn:
        out.append(fn)
    for part in payload.get("parts") or []:
        out.extend(_collect_filenames(part))
    return out


def _header_map(msg):
    headers = {}
    for h in (msg.get("payload", {}) or {}).get("headers", []) or []:
        if isinstance(h, dict) and "name" in h and "value" in h:
            headers[str(h["name"]).lower()] = str(h["value"])
    return headers


def _contains_all(haystack, needles):
    if needles is None:
        return True
    if isinstance(needles, str):
        needles = [needles]
    for n in needles:
        if str(n) not in str(haystack):
            return False
    return True


def verify_draft(access_token, draft_id, expected_to=None, expected_cc=None,
                 expected_subject=None, expected_attachment=None, timeout=30):
    url = DRAFTS_URL + "/" + urllib.parse.quote(str(draft_id), safe="") + "?format=full"
    req = urllib.request.Request(
        url,
        headers={"Authorization": "Bearer " + str(access_token)},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise DraftVerificationError("draft verification failed: HTTP " + str(e.code))
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
        raise DraftVerificationError("draft verification failed: " + type(e).__name__)
    if not isinstance(body, dict):
        raise DraftVerificationError("draft verification failed: bad response")
    msg = body.get("message", {})
    if not isinstance(msg, dict):
        msg = {}
    headers = _header_map(msg)
    to_val = headers.get("to", "")
    cc_val = headers.get("cc", "")
    subject = headers.get("subject", "")
    filenames = _collect_filenames(msg.get("payload", {}))
    problems = []
    if expected_to is not None and not _contains_all(to_val, expected_to):
        problems.append("to mismatch")
    if expected_cc is not None and not _contains_all(cc_val, expected_cc):
        problems.append("cc mismatch")
    if expected_subject is not None and subject != str(expected_subject):
        problems.append("subject mismatch")
    if expected_attachment is not None:
        if isinstance(expected_attachment, str):
            expected_attachment = [expected_attachment]
        for fn in expected_attachment:
            if str(fn) not in filenames:
                problems.append("attachment missing: " + str(fn))
    if problems:
        raise DraftVerificationError(
            "draft " + str(draft_id) + " NOT created-verified: " + "; ".join(problems)
        )
    return {
        "draft_id": str(draft_id),
        "to": to_val,
        "cc": cc_val,
        "subject": subject,
        "attachments": filenames,
        "verified": True,
    }


def create_and_verify(to, cc=None, subject="", body="", attachments=None,
                     max_bytes=25 * 1024 * 1024, timeout=30,
                     token_path=None, config_path=None, account_email=None):
    msg = build_message(to, cc, subject, body, attachments)
    raw_bytes = message_to_bytes(msg)
    if len(raw_bytes) > max_bytes:
        raise GmailDraftError("message exceeds --max-bytes: " + str(len(raw_bytes)))
    raw = b64url_encode(raw_bytes)
    refresh_token = get_refresh_token(token_path, account_email=account_email)
    client_id, client_secret = get_client_credentials(config_path)
    access_token = refresh_access_token(refresh_token, client_id, client_secret, timeout=timeout)
    draft_id = create_draft(access_token, raw, timeout=timeout)
    expected_attachments = [sanitize_filename(Path(a).name) for a in (attachments or [])]
    result = verify_draft(
        access_token,
        draft_id,
        expected_to=to if not isinstance(to, (list, tuple)) else list(to),
        expected_cc=cc if (cc is None or isinstance(cc, str)) else list(cc),
        expected_subject=subject,
        expected_attachment=expected_attachments if expected_attachments else None,
        timeout=timeout,
    )
    return result


def _print_summary(msg, to, cc, attachments, raw_bytes):
    print("To:", ", ".join(to) if isinstance(to, (list, tuple)) else to)
    print("Cc:", (", ".join(cc) if isinstance(cc, (list, tuple)) else cc) if cc else "(none)")
    print("Subject:", msg["Subject"])
    print("Bytes:", len(raw_bytes))
    names = [sanitize_filename(Path(a).name) for a in (attachments or [])]
    print("Attachments:", ", ".join(names) if names else "(none)")
    parts = 1
    if msg.is_multipart():
        parts = len(msg.get_payload())
    print("MIME:", msg.get_content_type(), "(" + str(parts) + " part" + ("s" if parts != 1 else "") + ")")


def main(argv=None):
    parser = argparse.ArgumentParser(prog="ja.gmail_draft")
    parser.add_argument("--to", nargs="+", required=True)
    parser.add_argument("--cc", nargs="*", default=None)
    parser.add_argument("--subject", required=True)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--body", default=None)
    group.add_argument("--body-file", default=None)
    parser.add_argument("--attach", action="append", default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--account", default=None)
    args = parser.parse_args(argv)

    if args.body_file:
        body = Path(args.body_file).read_text(encoding="utf-8")
    else:
        body = args.body if args.body is not None else ""

    to = args.to
    cc = args.cc
    attachments = args.attach

    msg = build_message(to, cc, body=body, subject=args.subject, attachments=attachments)
    raw_bytes = message_to_bytes(msg)

    if len(raw_bytes) > args.max_bytes:
        print("error: message exceeds --max-bytes (" + str(len(raw_bytes)) + " > " + str(args.max_bytes) + ")")
        return 1

    if args.dry_run:
        _print_summary(msg, to, cc, attachments, raw_bytes)
        return 0

    try:
        result = create_and_verify(
            to=to,
            cc=cc,
            subject=args.subject,
            body=body,
            attachments=attachments,
            max_bytes=args.max_bytes,
            token_path=TOKEN_STORE_PATH,
            config_path=MCP_CONFIG_PATH,
        )
    except GmailDraftError as e:
        print("error: " + str(e))
        return 1

    if args.verify:
        print("draft_id:", result["draft_id"])
        print("to:", result["to"])
        print("subject:", result["subject"])
        print("attachments:", ", ".join(result["attachments"]) if result["attachments"] else "(none)")
        print("verified: true")
    else:
        print("draft_id:", result["draft_id"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
