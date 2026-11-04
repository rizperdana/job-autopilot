"""Offline tests for the daily loop (stdlib unittest, no network, no DB dependency)."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ja import config, loop


def _write_dataset_fixture(base):
    """Minimal valid dataset: 1 company, 1 live role, in all four files."""
    base.mkdir(parents=True, exist_ok=True)
    base.joinpath("dataset.jsonl").write_text(json.dumps({
        "name": "Acme", "slug": "acme", "website": "https://acme.com",
        "careers_url": "https://acme.com/careers", "region": "worldwide",
        "remote_policy": "fully-remote", "company_size": "small",
        "technologies": ["python"], "addedAt": "2024-01-01",
        "updatedAt": "2024-01-02", "redirectFrom": None,
        "description": "desc"}) + "\n")
    base.joinpath("liveness.jsonl").write_text(json.dumps({
        "name": "Acme", "url": "https://acme.com",
        "careers_url": "https://acme.com/careers", "ats": "greenhouse",
        "slug": "acme", "open_roles": 3, "sample_titles": [],
        "http_status": 200, "checked_at": "2026-11-03T00:00:00+00:00",
        "error": None}) + "\n")
    base.joinpath("pass2.jsonl").write_text(json.dumps({
        "name": "Acme", "slug": "acme",
        "careers_url": "https://acme.com/careers", "ats_embed": "greenhouse",
        "ats_slug": "acme", "open_roles": 3,
        "titles": ["Senior Software Engineer"], "locations": ["Remote"],
        "jsonld_jobposting": False, "apply_channel": "form",
        "email_addresses": [], "worldwide_phrases": [],
        "exclusion_phrases": [], "http_status": 200, "error": None}) + "\n")
    base.joinpath("postings.jsonl").write_text(json.dumps({
        "company": "Acme", "slug": "acme", "ats": "greenhouse",
        "ats_slug": "acme", "title": "Senior Software Engineer",
        "location": "Remote",
        "url": "https://boards.greenhouse.io/acme/jobs/123",
        "apply_url": "https://boards.greenhouse.io/acme/jobs/123",
        "source": "test"}) + "\n")


def _job(job_id="j1", company="Acme", role="Engineer", channel="email"):
    return {"job_id": job_id, "company": company, "role": role, "channel": channel,
            "url": "https://x.example", "apply_url": "https://x.example/apply",
            "ats": "greenhouse", "source": "test", "location_text": "",
            "worldwide_ok": True, "posted_at": None, "dedupe_hash": "h" + job_id}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.co = self.tmp / "career-ops"
        (self.co / "data").mkdir(parents=True)
        self.rt = self.tmp / "runtime"
        self._orig = (config.career_ops_dir, config.runtime_dir, config.profile,
                      config.dataset_path, config.allow_automated_submit)
        config.career_ops_dir = lambda: self.co
        config.runtime_dir = lambda: self.rt
        config.profile = lambda: {"candidate": {"full_name": "Test User"}}
        ds = self.tmp / "datasets"
        _write_dataset_fixture(ds)
        config.dataset_path = lambda: ds
        config.allow_automated_submit = lambda: False
        import ja.tracker as tracker
        self.tracker = tracker
        tracker.init_db()
        self.addCleanup(self._restore)

    def _restore(self):
        (config.career_ops_dir, config.runtime_dir, config.profile,
         config.dataset_path, config.allow_automated_submit) = self._orig


    def test_n_plus_1th_draft_refused(self):
        cfg = {"policy": {"max_drafts_per_day": 2, "max_submissions_per_day": 0}}
        with mock.patch("ja.channels.email.create_email_draft", return_value="mock-draft-id"), \
             mock.patch.object(config, "get", lambda p, d=None: _get(cfg, p, d)), \
             mock.patch.object(loop, "already_drafted", lambda jid: None):
            r1 = loop.route_job(_job("a"), dry=False, drafts_today=0,
                                profile={}, max_drafts=2)
            self.assertEqual(r1[0], "drafted")
            r2 = loop.route_job(_job("b"), dry=False, drafts_today=1,
                                profile={}, max_drafts=2)
            self.assertEqual(r2[0], "drafted")
            r3 = loop.route_job(_job("c"), dry=False, drafts_today=2,
                                profile={}, max_drafts=2)
            self.assertEqual(r3[0], "skip")
            self.assertIn("cap reached", r3[1])


class TestDryRun(Base):
    def test_dry_run_never_calls_draft_fn(self):
        with mock.patch("ja.channels.email.create_email_draft") as m:
            a, why = loop.route_job(_job(), dry=True, drafts_today=0,
                                    profile={}, max_drafts=10)
            self.assertEqual(a, "would_draft")
            m.assert_not_called()


class TestIdempotency(Base):
    def test_already_drafted_skipped(self):
        with mock.patch.object(loop, "already_drafted", lambda jid: "drafted"):
            with mock.patch("ja.channels.email.create_email_draft") as m:
                a, why = loop.route_job(_job(), dry=False, drafts_today=0,
                                        profile={}, max_drafts=10)
                self.assertEqual(a, "skip")
                self.assertIn("already", why)
                m.assert_not_called()


class TestSubmitGuard(Base):
    def test_no_submit_when_config_false(self):
        calls = []

        def fake_prepare(job, profile, **kw):
            calls.append(kw)
            self.assertFalse(kw.get("allow_submit"))
            return {"submitted": False, "needs_human": [], "artifacts": {}}

        with mock.patch("ja.channels.form.prepare_application", fake_prepare):
            a, _ = loop.route_job(_job(channel="form"), dry=False, drafts_today=0,
                                  profile={}, max_drafts=10, confirm_submit=True)
            self.assertEqual(a, "prefilled")
            self.assertTrue(calls and not calls[0].get("allow_submit"))

    def test_confirm_false_blocks_even_if_allow_true(self):
        calls = []

        def fake_prepare(job, profile, **kw):
            calls.append(kw)
            return {"submitted": False, "needs_human": [], "artifacts": {}}

        with mock.patch.object(config, "allow_automated_submit", lambda: True), \
             mock.patch("ja.channels.form.prepare_application", fake_prepare):
            loop.route_job(_job(channel="form"), dry=False, drafts_today=0,
                           profile={}, max_drafts=10, confirm_submit=False)
            self.assertFalse(calls[0].get("confirm_submit"))


class TestStateUpdated(Base):
    def test_run_updates_state_json(self):
        rt = config.runtime_dir()
        rt.mkdir(parents=True, exist_ok=True)
        loop.run(limit=5, dry=True)
        sp = config.runtime_dir() / "state.json"
        self.assertTrue(sp.exists())
        st = json.loads(sp.read_text())
        self.assertEqual(st["phase"], "P6-loop")
        self.assertIn("last_updated", st)
        self.assertIn("current_task", st)

    def test_run_dry_no_events_writes_beyond_log(self):
        rt = config.runtime_dir()
        rt.mkdir(parents=True, exist_ok=True)
        loop.run(limit=5, dry=True)
        import datetime
        p = rt / "runs" / datetime.date.today().isoformat() / "events.jsonl"
        if p.exists():
            for line in p.read_text().splitlines():
                ev = json.loads(line)
                self.assertNotIn(ev["kind"], ("draft_created", "prefill_done"))


def _get(cfg, path, default=None):
    cur = cfg
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur


if __name__ == "__main__":
    unittest.main()
