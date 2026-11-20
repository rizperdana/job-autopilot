"""Email channel wrapper around ja.gmail_draft (drafts only)."""
import re
from pathlib import Path

from .. import gmail_draft as _gd

build_message = _gd.build_message
message_to_bytes = _gd.message_to_bytes
b64url_encode = _gd.b64url_encode
b64url_decode = _gd.b64url_decode
sanitize_filename = _gd.sanitize_filename
get_refresh_token = _gd.get_refresh_token
get_client_credentials = _gd.get_client_credentials
refresh_access_token = _gd.refresh_access_token
create_draft = _gd.create_draft
verify_draft = _gd.verify_draft
create_and_verify = _gd.create_and_verify

_OPENER_RE = re.compile(r"^Hi\s+\S.*\steam,$")

def _resolve_signer():
    """The signature the email body actually uses: the profile's first name,
    read at runtime so no personal name is hardcoded in the repo."""
    try:
        from .. import config
        name = ((config.profile().get("candidate") or {}).get("full_name") or "").strip()
        return name.split()[0] if name else ""
    except Exception:
        return ""

def _cv_on_disk(attachments):
    for a in attachments or []:
        try:
            if Path(str(a)).is_file():
                return True
        except Exception:
            continue
    return False

def _confidence(to, subject, body, attachments=None, signer=None):
    """Pure: percentage of the seven outbound-email facts that actually hold.

    One point per fact; anything we cannot verify counts against us (never
    inflate). total is always 7, so pct is never null here.
    """
    if signer is None:
        signer = _resolve_signer()
    lines = [l.strip() for l in (body or "").splitlines() if l.strip()]
    text = "\n".join(lines)
    checks = [
        ("recipient", bool(to)),
        ("subject", bool((subject or "").strip())),
        ("opener", bool(_OPENER_RE.match(lines[0])) if lines else False),
        ("CV attached line", "My CV is attached." in text),
        ("signature", bool(signer) and any(l.lower() == signer.lower() for l in lines[-4:])),
        ("CV file on disk", _cv_on_disk(attachments)),
        ("body length", 80 <= len(text.split()) <= 200),
    ]
    filled = sum(1 for _, ok in checks if ok)
    return {
        "pct": int(round(100.0 * filled / 7)),
        "method": "email-facts",
        "total": 7,
        "filled": filled,
        "missing": [label for label, ok in checks if not ok][:8],
        "channel": "email",
        # same per-item shape the form channel emits, so the dashboard has one path
        "fields": [{"label": label, "required": True, "score": 100 if ok else 0,
                    "state": "filled" if ok else "missing"} for label, ok in checks],
        "skipped_optional": 0,
    }

def create_email_draft(to, cc=None, subject="", body="", attachments=None,
                      verify=False, max_bytes=25 * 1024 * 1024, timeout=30,
                      token_path=None, config_path=None):
    # measured before the draft is created: these are the facts we vouch for
    confidence = _confidence(to, subject, body, attachments)
    result = create_and_verify(
        to=to,
        cc=cc,
        subject=subject,
        body=body,
        attachments=attachments,
        max_bytes=max_bytes,
        timeout=timeout,
        token_path=token_path,
        config_path=config_path,
    )
    if isinstance(result, dict):
        result["confidence"] = confidence
    if not verify:
        return result["draft_id"]
    return result

draft = create_email_draft
draft_email = create_email_draft
