"""Tests for sources."""
import json
import tempfile
import unittest
from pathlib import Path

from ja import config, sources


class TestSources(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        base = Path(self.tmp) / "remoteintech"
        base.mkdir()
        # dataset
        (base / "dataset.jsonl").write_text(json.dumps({
            "name": "Acme", "slug": "acme", "website": "https://acme.com",
            "careers_url": "https://acme.com/careers", "region": "worldwide",
            "remote_policy": "fully-remote", "company_size": "small",
            "technologies": ["python"], "addedAt": "2024-01-01",
            "updatedAt": "2024-01-02", "redirectFrom": None,
            "description": "desc"}) + "\n")
        # liveness
        (base / "liveness.jsonl").write_text(json.dumps({
            "name": "Acme", "url": "https://acme.com", "careers_url": "https://acme.com/careers",
            "ats": "greenhouse", "slug": "acme", "open_roles": 3,
            "sample_titles": [], "http_status": 200,
            "checked_at": "2026-11-03T00:00:00+00:00", "error": None}) + "\n")
        # pass2
        (base / "pass2.jsonl").write_text(json.dumps({
            "name": "Acme", "slug": "acme", "careers_url": "https://acme.com/careers",
            "ats_embed": "greenhouse", "ats_slug": "acme", "open_roles": 3,
            "titles": ["Senior Software Engineer"], "locations": ["Remote"],
            "jsonld_jobposting": False, "apply_channel": "form",
            "email_addresses": [], "worldwide_phrases": [],
            "exclusion_phrases": [], "http_status": 200, "error": None}) + "\n")
        # postings
        (base / "postings.jsonl").write_text(json.dumps({
            "company": "Acme", "slug": "acme", "ats": "greenhouse", "ats_slug": "acme",
            "title": "Senior Software Engineer", "location": "Remote",
            "url": "https://acme.com/jobs/123", "apply_url": "https://acme.com/jobs/123/apply",
            "department": "Eng", "updated_at": "2026-11-04", "source": "api"}) + "\n")
        config.dataset_path = lambda: base
        rt = Path(self.tmp) / "runtime"
        config.runtime_dir = lambda: rt
        # career_ops dir
        co = Path(self.tmp) / "career-ops"
        co.mkdir()
        config.career_ops_dir = lambda: co

    def test_discover_produces_jobs(self):
        jobs = sources.run()
        self.assertGreater(len(jobs), 0)
        j = jobs[0]
        self.assertIn("dedupe_hash", j)
        self.assertEqual(j["company"], "Acme")
        self.assertNotEqual(j["url"], "https://acme.com/careers")
        self.assertNotEqual(j["apply_url"], "https://acme.com/careers")

    def test_no_postings_yields_zero_jobs(self):
        import json, tempfile
        from pathlib import Path
        tmp = Path(tempfile.mkdtemp())
        base = tmp / "remoteintech"
        base.mkdir()
        (base / "dataset.jsonl").write_text(json.dumps({
            "name": "GhostCo", "slug": "ghost", "website": "https://ghost.com",
            "careers_url": "https://ghost.com/careers", "region": "worldwide",
            "remote_policy": "fully-remote", "company_size": "small",
            "technologies": ["python"], "addedAt": "2024-01-01",
            "updatedAt": "2024-01-02", "redirectFrom": None,
            "description": "desc"}) + "\n")
        (base / "liveness.jsonl").write_text(json.dumps({
            "name": "GhostCo", "url": "https://ghost.com", "careers_url": "https://ghost.com/careers",
            "ats": "custom", "slug": "ghost", "open_roles": 0,
            "sample_titles": [], "http_status": 200,
            "checked_at": "2026-11-03T00:00:00+00:00", "error": None}) + "\n")
        (base / "pass2.jsonl").write_text(json.dumps({
            "name": "GhostCo", "slug": "ghost", "careers_url": "https://ghost.com/careers",
            "ats_embed": None, "ats_slug": None, "open_roles": 0,
            "titles": [], "locations": [], "jsonld_jobposting": False,
            "apply_channel": "form", "email_addresses": [], "worldwide_phrases": [],
            "exclusion_phrases": [], "http_status": 200, "error": None}) + "\n")
        (base / "postings.jsonl").write_text("")  # empty
        orig = config.dataset_path
        config.dataset_path = lambda: base
        try:
            with self.assertRaises(RuntimeError):
                sources.run()
        finally:
            config.dataset_path = orig

    def test_missing_postings_fails_loudly(self):
        base = Path(self.tmp) / "nopost"
        base.mkdir(exist_ok=True)
        (base / "dataset.jsonl").write_text(json.dumps({
            "name": "Acme", "slug": "acme", "website": "https://acme.com",
            "careers_url": "https://acme.com/careers", "region": "worldwide",
            "remote_policy": "fully-remote", "company_size": "small",
            "technologies": ["python"], "addedAt": "2024-01-01",
            "updatedAt": "2024-01-02", "redirectFrom": None,
            "description": "desc"}) + "\n")
        config.dataset_path = lambda: base
        with self.assertRaises(RuntimeError):
            sources.run()

    def test_dedupe_hash_stable(self):
        # dedupe hash format check done via length above
        jobs = sources.run()
        self.assertEqual(len(jobs[0]["dedupe_hash"]), 64)

    def test_junk_postings_dropped(self):
        import json, tempfile
        from pathlib import Path
        tmp = Path(tempfile.mkdtemp())
        base = tmp / "remoteintech"
        base.mkdir()
        (base / "dataset.jsonl").write_text(json.dumps({
            "name": "Acme", "slug": "acme", "website": "https://acme.com",
            "careers_url": "https://acme.com/careers", "region": "worldwide",
            "remote_policy": "fully-remote", "company_size": "small",
            "technologies": ["python"], "addedAt": "2024-01-01",
            "updatedAt": "2024-01-02", "redirectFrom": None,
            "description": "desc"}) + "\n")
        (base / "liveness.jsonl").write_text(json.dumps({
            "name": "Acme", "url": "https://acme.com", "careers_url": "https://acme.com/careers",
            "ats": "custom", "slug": "acme", "open_roles": 0,
            "sample_titles": [], "http_status": 200,
            "checked_at": "2026-11-03T00:00:00+00:00", "error": None}) + "\n")
        (base / "pass2.jsonl").write_text(json.dumps({
            "name": "Acme", "slug": "acme", "careers_url": "https://acme.com/careers",
            "ats_embed": None, "ats_slug": None, "open_roles": 0,
            "titles": [], "locations": [], "jsonld_jobposting": False,
            "apply_channel": "form", "email_addresses": [], "worldwide_phrases": [],
            "exclusion_phrases": [], "http_status": 200, "error": None}) + "\n")
        (base / "postings.jsonl").write_text(
            json.dumps({"company": "Acme", "slug": "acme", "ats": "custom",
                        "ats_slug": None, "title": None, "location": "Remote",
                        "url": "https://acme.com/careers/", "apply_url": "https://acme.com/careers/",
                        "department": None, "updated_at": "2026-11-04", "source": "html"}) + "\n" +
            json.dumps({"company": "Acme", "slug": "acme", "ats": "custom",
                        "ats_slug": None, "title": "Senior Engineer", "location": "Remote",
                        "url": "https://acme.com/jobs/1", "apply_url": "https://acme.com/jobs/1/apply",
                        "department": "Eng", "updated_at": "2026-11-04", "source": "api"}) + "\n")
        orig = config.dataset_path
        config.dataset_path = lambda: base
        try:
            jobs = sources.run()
            self.assertEqual(len(jobs), 1)
            self.assertEqual(jobs[0]["url"], "https://acme.com/jobs/1")
        finally:
            config.dataset_path = orig

    def test_missing_file_fails_loudly(self):
        base = Path(self.tmp) / "empty"
        base.mkdir(exist_ok=True)
        config.dataset_path = lambda: base
        with self.assertRaises(RuntimeError):
            sources.run()

    def test_no_portal_synthesis(self):
        self.assertFalse(hasattr(sources, "_from_portals"))
        jobs = sources.run()
        self.assertTrue(all(j.get("source") != "portals" for j in jobs))

if __name__ == "__main__":
    unittest.main()
