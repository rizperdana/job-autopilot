"""Tests for tracker."""
import os
import tempfile
import unittest
from pathlib import Path

from ja import config, tracker


class TestTracker(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        # point career_ops to tmp by monkeypatching config
        self.orig = config.career_ops_dir
        config.career_ops_dir = lambda: Path(self.tmp) / "career-ops"
        (Path(self.tmp) / "career-ops" / "data").mkdir(parents=True, exist_ok=True)
        tracker.init_db()

    def tearDown(self):
        config.career_ops_dir = self.orig

    def test_init_idempotent(self):
        tracker.init_db()
        tracker.init_db()
        self.assertTrue(tracker.db_path().exists())

    def test_valid_status_transition(self):
        tracker.upsert_job({"job_id": "j1", "company": "C", "role": "R",
                            "url": "u", "apply_url": "u", "ats": "x", "channel": "form",
                            "source": "s", "location_text": "", "worldwide_ok": True,
                            "posted_at": None, "gate_decision": "apply", "gate_score": 70,
                            "gate_reasons": [], "dedupe_hash": "h"})
        aid = tracker.create_application("j1", status="discovered")
        self.assertIsNotNone(aid)
        tracker.set_status(aid, "shortlisted")
        # invalid
        with self.assertRaises(ValueError):
            tracker.set_status(aid, "bogus")

    def test_stats_keys(self):
        st = tracker.stats()
        self.assertIn("jobs_total", st)
        self.assertIn("gate_decisions", st)

    def test_sync_writes_tsv(self):
        tracker.upsert_job({"job_id": "j2", "company": "C2", "role": "R2",
                            "url": "u", "apply_url": "u", "ats": "x", "channel": "form",
                            "source": "s", "location_text": "", "worldwide_ok": True,
                            "posted_at": None, "gate_decision": "apply", "gate_score": 70,
                            "gate_reasons": [], "dedupe_hash": "h2"})
        tracker.create_application("j2", status="discovered")
        p = tracker.sync()
        self.assertTrue(Path(p).exists())
        content = Path(p).read_text()
        self.assertIn("| # | Date | Company | Role | Score | Status | PDF | Report | Notes |", content)
        self.assertNotIn(".md", str(p))


if __name__ == "__main__":
    unittest.main()
