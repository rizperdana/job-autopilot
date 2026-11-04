"""Tests for gates."""
import unittest
from ja import config, gates, scam


class TestGates(unittest.TestCase):
    def test_evaluate_rejects_scam(self):
        job = {"job_id": "x", "company": "BadCo", "role": "Data Entry Clerk",
               "url": "http://x", "apply_url": "http://x", "ats": "custom",
               "channel": "form", "location_text": "data entry typing work",
               "worldwide_ok": True, "posted_at": None, "source": "remoteintech"}
        r = gates.evaluate(job)
        self.assertEqual(r["decision"], "reject_hard")
        self.assertEqual(r["failed_gate"], "G7")

    def test_g2_missing_liveness_review(self):
        job = {"job_id": "y", "company": "Acme", "role": "Senior Software Engineer",
               "url": "http://x", "apply_url": "http://x", "ats": "custom",
               "channel": "form", "location_text": "Remote", "worldwide_ok": True,
               "posted_at": None, "source": "remoteintech"}
        r = gates.evaluate(job)
        g2 = [g for g in r["gate_results"] if g["gate"] == "G2"][0]
        self.assertEqual(g2["status"], "review")

    def test_g2_posted_old_fail(self):
        job = {"job_id": "z", "company": "Acme", "role": "Senior Software Engineer",
               "url": "http://x", "apply_url": "http://x", "ats": "custom",
               "channel": "form", "location_text": "Remote", "worldwide_ok": True,
               "posted_at": "2020-01-01", "source": "remoteintech"}
        r = gates.evaluate(job)
        self.assertEqual(r["decision"], "reject_hard")
        self.assertEqual(r["failed_gate"], "G2")

    def test_g4_language(self):
        job = {"job_id": "l", "company": "Acme", "role": "Senior Software Engineer",
               "url": "http://x", "apply_url": "http://x", "ats": "custom",
               "channel": "form", "location_text": "Tokyo, Japanese native required",
               "worldwide_ok": True, "posted_at": "2026-11-01", "source": "remoteintech"}
        r = gates.evaluate(job)
        self.assertEqual(r["decision"], "reject_hard")
        self.assertEqual(r["failed_gate"], "G4")

    def test_scored_thresholds(self):
        job = {"job_id": "s", "company": "Acme", "role": "Senior Software Engineer",
               "url": "http://x", "apply_url": "http://x", "ats": "custom",
               "channel": "form", "location_text": "Remote worldwide", "worldwide_ok": True,
               "posted_at": "2026-11-01", "source": "remoteintech"}
        r = gates.evaluate(job)
        self.assertIn(r["decision"], ("apply", "review", "skip"))
        if r["breakdown"]:
            self.assertIn("role_fit", r["breakdown"])

    def test_g3_us_auth_no_sponsor(self):
        job = {"job_id": "g3", "company": "Acme", "role": "Senior Software Engineer",
               "url": "http://x", "apply_url": "http://x", "ats": "custom",
               "channel": "form", "location_text": "Remote, authorized to work in the us",
               "worldwide_ok": False, "posted_at": "2026-11-01", "source": "remoteintech"}
        r = gates.evaluate(job)
        self.assertEqual(r["decision"], "reject_hard")
        self.assertEqual(r["failed_gate"], "G3")

    def test_g3_exclusion_precedence(self):
        import json, tempfile, os
        from pathlib import Path
        tmp = Path(tempfile.mkdtemp())
        base = tmp / "remoteintech"
        base.mkdir()
        (base / "dataset.jsonl").write_text(json.dumps({
            "name": "Close", "slug": "close", "website": "https://close.com",
            "careers_url": "https://close.com/jobs", "region": "worldwide",
            "remote_policy": "fully-remote", "company_size": "small",
            "technologies": ["python"], "addedAt": "2024-01-01",
            "updatedAt": "2024-01-02", "redirectFrom": None,
            "description": "desc"}) + "\n")
        (base / "pass2.jsonl").write_text(json.dumps({
            "name": "Close", "slug": "close", "careers_url": "https://close.com/jobs",
            "ats_embed": "ashby", "ats_slug": "close", "open_roles": 5,
            "titles": ["Senior Software Engineer – Backend (USA Only, 100% Remote)"],
            "locations": ["USA - Remote"], "jsonld_jobposting": False,
            "apply_channel": "form", "email_addresses": [],
            "worldwide_phrases": ["anywhere (careers page)"],
            "exclusion_phrases": [], "http_status": 200, "error": None}) + "\n")
        orig = config.dataset_path
        config.dataset_path = lambda: base
        try:
            job = {"job_id": "c", "company": "Close",
                   "role": "Senior Software Engineer – Backend (USA Only, 100% Remote)",
                   "url": "https://close.com/jobs", "apply_url": "https://close.com/jobs",
                   "ats": "ashby", "channel": "form", "location_text": "USA - Remote",
                   "worldwide_ok": True, "posted_at": None, "source": "remoteintech"}
            r = gates.evaluate(job)
            self.assertEqual(r["decision"], "reject_hard")
            self.assertEqual(r["failed_gate"], "G3")
            g3 = [g for g in r["gate_results"] if g["gate"] == "G3"][0]
            self.assertIn("usa only", g3["reason"].lower())
        finally:
            config.dataset_path = orig

    def test_g3_ambiguous_downgrades(self):
        job = {"job_id": "a", "company": "Acme", "role": "Senior Software Engineer",
               "url": "http://x", "apply_url": "http://x", "ats": "custom",
               "channel": "form", "location_text": "Remote",
               "worldwide_ok": None, "posted_at": "2026-11-01", "source": "remoteintech"}
        r = gates.evaluate(job)
        g3 = [g for g in r["gate_results"] if g["gate"] == "G3"][0]
        self.assertIn("ambiguous", g3["reason"].lower())

    def test_g6_blacklist(self):
        d = config.career_ops_dir()
        bp = d / "data" / "blacklist.md"
        existed = bp.exists()
        old = bp.read_text() if existed else None
        bp.parent.mkdir(parents=True, exist_ok=True)
        bp.write_text("evilcorp\n")
        try:
            job = {"job_id": "b", "company": "EvilCorp", "role": "Senior Software Engineer",
                   "url": "http://x", "apply_url": "http://x", "ats": "custom",
                   "channel": "form", "location_text": "Remote", "worldwide_ok": True,
                   "posted_at": "2026-11-01", "source": "remoteintech"}
            r = gates.evaluate(job)
            self.assertEqual(r["decision"], "reject_hard")
            self.assertEqual(r["failed_gate"], "G6")
        finally:
            if existed:
                bp.write_text(old)
            else:
                try:
                    bp.unlink()
                except FileNotFoundError:
                    pass

    def test_g2_open_roles_live(self):
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
        (base / "pass2.jsonl").write_text(json.dumps({
            "name": "Acme", "slug": "acme", "careers_url": "https://acme.com/careers",
            "ats_embed": "greenhouse", "ats_slug": "acme", "open_roles": 3,
            "titles": ["Senior Software Engineer"], "locations": ["Remote"],
            "jsonld_jobposting": False, "apply_channel": "form",
            "email_addresses": [], "worldwide_phrases": [],
            "exclusion_phrases": [], "http_status": 200, "error": None}) + "\n")
        orig = config.dataset_path
        config.dataset_path = lambda: base
        try:
            job = {"job_id": "g2", "company": "Acme", "role": "Senior Software Engineer",
                   "url": "https://acme.com/careers", "apply_url": "https://acme.com/careers",
                   "ats": "greenhouse", "channel": "form", "location_text": "Remote",
                   "worldwide_ok": True, "posted_at": None, "source": "remoteintech"}
            r = gates.evaluate(job)
            g2 = [g for g in r["gate_results"] if g["gate"] == "G2"][0]
            self.assertEqual(g2["status"], "pass")
            self.assertIn("live-verified", g2["reason"])
        finally:
            config.dataset_path = orig

    def test_g3_us_remote_title_reject(self):
        job = {"job_id": "ur", "company": "Acme", "role": "Engineer (US Remote)",
               "url": "http://x", "apply_url": "http://x", "ats": "custom",
               "channel": "form", "location_text": "Remote", "worldwide_ok": True,
               "posted_at": "2026-11-01", "source": "remoteintech"}
        r = gates.evaluate(job)
        self.assertEqual(r["decision"], "reject_hard")
        self.assertEqual(r["failed_gate"], "G3")

    def test_g3_bare_remote_review(self):
        job = {"job_id": "br", "company": "Acme", "role": "Senior Software Engineer",
               "url": "http://x", "apply_url": "http://x", "ats": "custom",
               "channel": "form", "location_text": "Remote", "worldwide_ok": None,
               "posted_at": "2026-11-01", "source": "remoteintech"}
        r = gates.evaluate(job)
        g3 = [g for g in r["gate_results"] if g["gate"] == "G3"][0]
        self.assertEqual(g3["status"], "review")

    def test_g3_worldwide_company_region_locked_job_reject(self):
        import json, tempfile
        from pathlib import Path
        tmp = Path(tempfile.mkdtemp())
        base = tmp / "remoteintech"
        base.mkdir()
        (base / "dataset.jsonl").write_text(json.dumps({
            "name": "WWCo", "slug": "wwco", "website": "https://wwco.com",
            "careers_url": "https://wwco.com/jobs", "region": "worldwide",
            "remote_policy": "fully-remote", "company_size": "small",
            "technologies": ["python"], "addedAt": "2024-01-01",
            "updatedAt": "2024-01-02", "redirectFrom": None,
            "description": "desc"}) + "\n")
        (base / "pass2.jsonl").write_text(json.dumps({
            "name": "WWCo", "slug": "wwco", "careers_url": "https://wwco.com/jobs",
            "ats_embed": "greenhouse", "ats_slug": "wwco", "open_roles": 3,
            "titles": ["Engineer"], "locations": ["US-REMOTE"],
            "jsonld_jobposting": False, "apply_channel": "form",
            "email_addresses": [], "worldwide_phrases": ["anywhere (careers page)"],
            "exclusion_phrases": [], "http_status": 200, "error": None}) + "\n")
        orig = config.dataset_path
        config.dataset_path = lambda: base
        try:
            job = {"job_id": "w", "company": "WWCo", "role": "Engineer",
                   "url": "https://wwco.com/jobs", "apply_url": "https://wwco.com/jobs",
                   "ats": "greenhouse", "channel": "form", "location_text": "US-REMOTE",
                   "worldwide_ok": True, "posted_at": None, "source": "remoteintech"}
            r = gates.evaluate(job)
            self.assertEqual(r["decision"], "reject_hard")
            self.assertEqual(r["failed_gate"], "G3")
        finally:
            config.dataset_path = orig


class TestScam(unittest.TestCase):
    FIXTURES_POS = [
        "Please pay a $50 application fee to proceed.",
        "Send payment in bitcoin to our wallet address.",
        "You must pay for training before starting.",
        "Buy your own laptop and equipment deposit required.",
        "Complete this unpaid trial project for free.",
        "Contact our recruiter at hiring@example.com for the role.",
        "Apply via WhatsApp only, no email.",
        "This is a commission-only 100% commission role.",
        "Join our multi-level downline team.",
        "Sell life insurance as an agent.",
        "Data entry clerk typing work from home.",
        "Wire transfer the onboarding fee.",
    ]
    FIXTURES_NEG = [
        "Senior Software Engineer, remote worldwide, Python and React.",
        "We offer competitive salary and benefits.",
        "Fully remote role, apply via our careers page.",
        "Machine Learning Engineer with LLM experience.",
        "Backend engineer, PostgreSQL, Docker, AWS.",
        "Frontend developer, React, TypeScript.",
        "DevOps engineer, CI/CD, Kubernetes.",
        "Fullstack engineer, REST APIs, microservices.",
    ]

    def test_positive(self):
        for t in self.FIXTURES_POS:
            hits = scam.scan(t)
            self.assertTrue(len(hits) > 0, t)

    def test_negative(self):
        for t in self.FIXTURES_NEG:
            hits = scam.scan(t)
            self.assertEqual(len(hits), 0, t + " -> " + str(hits))

    def test_returns_pattern_and_evidence(self):
        hits = scam.scan("Please pay a $50 application fee to proceed.")
        self.assertIn("pattern_id", hits[0])
        self.assertIn("evidence", hits[0])


class TestAggregatorFilter(unittest.TestCase):
    def test_aggregator_dropped(self):
        from ja import sources
        # simulate: pass2 emails containing privacy@ and team@wellfound
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
        (base / "pass2.jsonl").write_text(json.dumps({
            "name": "Acme", "slug": "acme", "careers_url": "https://acme.com/careers",
            "ats_embed": None, "ats_slug": None, "open_roles": None,
            "titles": [], "locations": [], "jsonld_jobposting": False,
            "apply_channel": "email", "email_addresses": ["privacy@example.com", "team@example.org", "careers@example.com"],
            "worldwide_phrases": [], "exclusion_phrases": [], "http_status": 200,
            "error": None}) + "\n")
        (base / "postings.jsonl").write_text(json.dumps({
            "company": "Acme", "slug": "acme", "ats": "custom", "ats_slug": None,
            "title": "Software Engineer", "location": "Remote",
            "url": "https://acme.com/jobs/1", "apply_url": "https://acme.com/jobs/1/apply",
            "department": "Eng", "updated_at": "2026-11-04", "source": "api"}) + "\n")
        orig = config.dataset_path
        config.dataset_path = lambda: base
        try:
            jobs = sources.run()
            raw = jobs[0]["raw"]
            self.assertEqual(raw["email_addresses"], ["careers@example.com"])
            self.assertIn("privacy@example.com", raw["dropped_emails"])
            self.assertIn("team@example.org", raw["dropped_emails"])
        finally:
            config.dataset_path = orig


if __name__ == "__main__":
    unittest.main()
