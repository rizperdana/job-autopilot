"""Offline tests for Gmail draft channel."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import ja.gmail_draft as gd


class TestBuildMime(unittest.TestCase):
    def test_to_cc_subject_and_attachment(self):
        with tempfile.TemporaryDirectory() as td:
            pdf = Path(td) / "sample.pdf"
            pdf.write_bytes(b"%PDF-1.4 fake pdf bytes")
            msg = gd.build_message(
                to=["a@example.com", "b@example.com"],
                cc="c@example.com",
                subject="Hello subject",
                body="body text",
                attachments=[str(pdf)],
            )
            self.assertEqual(msg["To"], "a@example.com, b@example.com")
            self.assertEqual(msg["Cc"], "c@example.com")
            self.assertEqual(msg["Subject"], "Hello subject")
            atts = []
            if msg.is_multipart():
                for part in msg.walk():
                    if part.get_content_disposition() == "attachment":
                        atts.append(part)
            self.assertEqual(len(atts), 1)
            att = atts[0]
            self.assertEqual(att.get_filename(), "sample.pdf")
            self.assertEqual(att.get_content_type(), "application/pdf")
            raw = att.get_payload(decode=True)
            self.assertEqual(raw, b"%PDF-1.4 fake pdf bytes")


class TestB64UrlRoundTrip(unittest.TestCase):
    def test_roundtrip(self):
        data = b"\x00\xff" * 50 + b"hello"
        enc = gd.b64url_encode(data)
        self.assertNotIn("=", enc)
        self.assertNotIn("+", enc)
        self.assertNotIn("/", enc)
        self.assertEqual(gd.b64url_decode(enc), data)


class TestMissingTokenRaises(unittest.TestCase):
    def test_missing_token_mentions_store_path(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "tokens.json"
            p.write_text(json.dumps({"cache": []}))
            with self.assertRaises(gd.TokenError) as ctx:
                gd.get_refresh_token(str(p))
            self.assertIn(str(p), str(ctx.exception))


class TestForbiddenEndpointGrep(unittest.TestCase):
    def test_no_send_endpoints_in_source(self):
        src = Path(__file__).resolve().parent.parent / "ja" / "gmail_draft.py"
        text = src.read_text(encoding="utf-8")
        for forbidden in ("users.messages.send", "users.drafts.send",
                          "messages.send", "drafts.send",
                          "/messages/send", "/drafts/send"):
            self.assertNotIn(forbidden, text, msg="forbidden endpoint found: " + forbidden)


class TestNoSendEndpointsInChannel(unittest.TestCase):
    def test_no_send_endpoints_in_channel_module(self):
        src = Path(__file__).resolve().parent.parent / "ja" / "channels" / "email.py"
        text = src.read_text(encoding="utf-8")
        for forbidden in ("users.messages.send", "users.drafts.send",
                          "messages.send", "drafts.send",
                          "/messages/send", "/drafts/send"):
            self.assertNotIn(forbidden, text, msg="forbidden endpoint found: " + forbidden)


class TestRefreshTokenParsing(unittest.TestCase):
    def test_parses_nested_value(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "tokens.json"
            entry = ["keyv:candidate@example.com:mcp-gmail:token",
                     {"expire": None,
                      "value": json.dumps({"value": {"refreshToken": "rt-abc"}})}]
            p.write_text(json.dumps({"cache": [entry]}))
            self.assertEqual(gd.get_refresh_token(str(p)), "rt-abc")


class TestRefreshAccessTokenMocked(unittest.TestCase):
    def test_refresh_success(self):
        resp = mock.MagicMock()
        resp.read.return_value = json.dumps({"access_token": "at-xyz"}).encode()
        cm = mock.MagicMock()
        cm.__enter__.return_value = resp
        cm.__exit__.return_value = False
        with mock.patch("ja.gmail_draft.urllib.request.urlopen", return_value=cm):
            tok = gd.refresh_access_token("rt", "cid", "secret")
        self.assertEqual(tok, "at-xyz")

    def test_refresh_non200_raises_with_code(self):
        import io
        import urllib.error
        err = urllib.error.HTTPError("u", 400, "bad", {}, None)
        err.fp = io.BytesIO(json.dumps({"error": "invalid_grant"}).encode())
        err.read = err.fp.read
        with mock.patch("ja.gmail_draft.urllib.request.urlopen", side_effect=err):
            with self.assertRaises(gd.OAuthRefreshError) as ctx:
                gd.refresh_access_token("rt", "cid", "secret")
        self.assertIn("invalid_grant", str(ctx.exception))


class TestCreateDraftMocked(unittest.TestCase):
    def test_create_draft_returns_id(self):
        resp = mock.MagicMock()
        resp.read.return_value = json.dumps({"id": "draft123"}).encode()
        cm = mock.MagicMock()
        cm.__enter__.return_value = resp
        cm.__exit__.return_value = False
        with mock.patch("ja.gmail_draft.urllib.request.urlopen", return_value=cm):
            did = gd.create_draft("tok", "rawpayload")
        self.assertEqual(did, "draft123")


class TestVerifyDraftMocked(unittest.TestCase):
    def test_verify_returns_fields(self):
        payload = {
            "message": {
                "payload": {
                    "headers": [
                        {"name": "To", "value": "a@example.com"},
                        {"name": "Subject", "value": "subj"},
                    ],
                    "parts": [{"filename": "x.pdf"}],
                }
            }
        }
        resp = mock.MagicMock()
        resp.read.return_value = json.dumps(payload).encode()
        cm = mock.MagicMock()
        cm.__enter__.return_value = resp
        cm.__exit__.return_value = False
        with mock.patch("ja.gmail_draft.urllib.request.urlopen", return_value=cm):
            res = gd.verify_draft("tok", "d1", expected_to="a@example.com",
                                  expected_subject="subj", expected_attachment="x.pdf")
        self.assertTrue(res["verified"])
        self.assertEqual(res["draft_id"], "d1")
        self.assertEqual(res["subject"], "subj")
        self.assertIn("x.pdf", res["attachments"])


class TestChannelsModule(unittest.TestCase):
    def test_imports(self):
        from ja.channels import email as em
        self.assertTrue(callable(em.create_email_draft))


if __name__ == "__main__":
    unittest.main()
