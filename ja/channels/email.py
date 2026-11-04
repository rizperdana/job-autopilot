"""Email channel wrapper around ja.gmail_draft (drafts only)."""
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


def create_email_draft(to, cc=None, subject="", body="", attachments=None,
                      verify=False, max_bytes=25 * 1024 * 1024, timeout=30,
                      token_path=None, config_path=None):
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
    if not verify:
        return result["draft_id"]
    return result


draft = create_email_draft
draft_email = create_email_draft
