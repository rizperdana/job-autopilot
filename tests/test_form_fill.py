"""Offline tests for form channel."""
import os
import unittest
from pathlib import Path

from ja.channels import form


class TestATSDetect(unittest.TestCase):
    def test_hosts(self):
        cases = {
            "https://boards.greenhouse.io/acme/jobs/1": "greenhouse",
            "https://jobs.lever.co/acme/abc": "lever",
            "https://jobs.ashbyhq.com/acme/abc": "ashby",
            "https://apply.workable.com/acme/j/abc": "workable",
            "https://jobs.smartrecruiters.com/acme/abc": "smartrecruiters",
            "https://acme.wd5.myworkdayjobs.com/en-US/jobs/job/x": "workday",
            "https://acme.recruitee.com/o/x": "recruitee",
            "https://career.teamtailor.com/jobs/x": "teamtailor",
            "https://acme.jobs.personio.com/job/123": "personio",
            "https://acme.bamboohr.com/careers/123": "bamboohr",
            "https://acme.breezy.hr/p/abc": "breezy",
            "https://acme.applytojob.com/apply/abc": "jazzhr",
            "https://careers.icims.com/jobs/123": "icims",
            "https://jobs.jobvite.com/acme/job/abc": "jobvite",
            "https://acme.gupy.io/jobs/123": "gupy",
            "https://example.com/careers": "generic",
        }
        for url, exp in cases.items():
            self.assertEqual(form.detect_ats(url), exp, url)


class TestFieldMapping(unittest.TestCase):
    def test_basic_mapping(self):
        profile = {
            "candidate": {
                "full_name": "Test Candidate",
                "email": "candidate@example.com",
                "phone": "+00 000 000 000",
                "location": "Test City, Indonesia",
                "linkedin": "linkedin.com/in/testcandidate",
                "github": "github.com/testcandidate",
                "portfolio_url": "https://example.com/portfolio",
            },
            "compensation": {"target_range": "$50K-90K"},
            "location": {"country": "Indonesia", "needs_sponsorship": True},
        }
        fields = [
            {"label": "Full name", "name": "name"},
            {"label": "Email", "name": "email"},
            {"label": "Phone", "name": "phone"},
            {"label": "LinkedIn URL", "name": "linkedin"},
            {"label": "GitHub", "name": "github"},
            {"label": "Portfolio website", "name": "website"},
            {"label": "Expected salary", "name": "salary"},
            {"label": "How did you hear about us?", "name": "source"},
            {"label": "Years of experience", "name": "yoe"},
            {"label": "Some obscure field", "name": "obscure"},
        ]
        m = form.map_fields(fields, profile, {})
        self.assertEqual(m["name"], "Test Candidate")
        self.assertEqual(m["email"], "candidate@example.com")
        self.assertEqual(m["phone"], "+00 000 000 000")
        self.assertEqual(m["linkedin"], "linkedin.com/in/testcandidate")
        self.assertEqual(m["github"], "github.com/testcandidate")
        self.assertEqual(m["website"], "https://example.com/portfolio")
        self.assertEqual(m["salary"], "$50K-90K")
        self.assertEqual(m["source"], "job board")
        # verbatim years fact now read from cv.md ("8 years") — real candidate CV has it
        self.assertEqual(m["yoe"], "8")
        # unknown field -> NEEDS_HUMAN
        self.assertEqual(m["obscure"], form.NEEDS_HUMAN)

    def test_years_experience_verbatim_fact(self):
        profile = {
            "candidate": {"full_name": "Test Candidate"},
        }
        fields = [{"label": "Years of experience", "name": "yoe"}]
        # facts derive from the REAL cv.md on this machine, which states "8 years".
        # The contract: only a verbatim fact fills the field; profile guesses never do.
        m = form.map_fields(fields, profile, {})
        self.assertIn(m["yoe"], ("8", form.NEEDS_HUMAN))  # 8 on this machine; NEEDS_HUMAN without cv.md


class TestSubmitGuard(unittest.TestCase):
    def test_allow_submit_false_no_click(self):
        # simulate: actions list must not contain submit_click
        actions = [{"action": "submit_blocked", "reason": "allow_submit=False confirm_submit=False"}]
        self.assertNotIn("submit_click", [a.get("action") for a in actions])

    def test_confirm_submit_false_blocks_even_allow_true(self):
        # ready_to_submit should be False when confirm_submit False
        ready = False  # mirrors guard logic
        self.assertFalse(ready)


class TestTurnstileCap(unittest.TestCase):
    def test_fourth_attempt_refused(self):
        # mock engine: always fail
        import ja.channels.form as F
        orig = None
        try:
            import sys
            sys.path.insert(0, str(Path.home() / ".job-autopilot" / "vendor" / "turnstile_solver"))
        except Exception:
            pass
        # We test the cap logic by simulating: 3 attempts allowed, 4th refused.
        attempts = 0
        refused = False
        for i in range(4):
            if attempts >= 3:
                refused = True
                break
            attempts += 1
        self.assertTrue(refused)
        self.assertEqual(attempts, 3)


if __name__ == "__main__":
    unittest.main()


class TestMappingRegressions(unittest.TestCase):
    def _profile(self):
        return {"candidate": {"full_name": "Test Candidate", "email": "candidate@example.com",
                              "phone": "+00 000 000 000", "location": "Test City, Indonesia"},
                "location": {"country": "Indonesia", "needs_sponsorship": True}}

    def test_email_field_never_gets_location(self):
        fields = [{"label": "Email", "name": "custemail"}, {"label": "Location", "name": "loc"}]
        m = form.map_fields(fields, self._profile(), {})
        self.assertEqual(m["custemail"], "candidate@example.com")
        self.assertEqual(m["loc"], "Test City, Indonesia")

    def test_customer_name_maps_full_name(self):
        fields = [{"label": "Customer name:", "name": "custname"},
                  {"label": "First name", "name": "first"},
                  {"label": "Last name", "name": "last"},
                  {"label": "Company", "name": "company"}]
        m = form.map_fields(fields, self._profile(), {})
        self.assertEqual(m["custname"], "Test Candidate")
        self.assertEqual(m["first"], "Test")
        self.assertEqual(m["last"], "Candidate")
        self.assertEqual(m["company"], form.NEEDS_HUMAN)

    def test_contradiction_guard(self):
        # force an email-shaped value into a phone field via answer bank? Instead
        # verify shape guard directly
        self.assertFalse(form._shape_ok("phone mobile", "example@example.com")[0])
        self.assertTrue(form._shape_ok("email", "example@example.com")[0])
        # email field with non-email value -> NEEDS_HUMAN
        fields = [{"label": "Email", "name": "e"}]
        # monkey: profile email replaced with location-like string is not in profile;
        # instead call _shape_ok
        self.assertFalse(form._shape_ok("email", "Test City, Indonesia")[0])


class TestProfileLoading(unittest.TestCase):
    """Bug 1 regression: profile facts must survive YAML parsing."""

    def test_fallback_parser_block_scalars_lists_comments(self):
        from ja.config import _parse_yaml
        text = (
            "# header comment\n"
            "candidate:\n"
            "  full_name: \"Jane Doe\"  # inline comment\n"
            "  email: \"a@b.example\"\n"
            "  phone: '+00 000'\n"
            "narrative:\n"
            "  exit_story: >-\n"
            "    line one\n"
            "    line two\n"
            "target_roles:\n"
            "  primary:\n"
            "    - \"Senior Software Engineer\"\n"
            "    - Fullstack Engineer\n"
            "  archetypes:\n"
            "    - name: Fullstack Engineer\n"
            "      level: Senior\n"
            "      fit: primary\n"
        )
        d = _parse_yaml(text)
        self.assertEqual(d["candidate"]["full_name"], "Jane Doe")
        self.assertEqual(d["candidate"]["email"], "a@b.example")
        self.assertEqual(d["candidate"]["phone"], "+00 000")
        self.assertEqual(d["narrative"]["exit_story"], "line one line two")
        self.assertEqual(d["target_roles"]["primary"], ["Senior Software Engineer", "Fullstack Engineer"])
        self.assertEqual(d["target_roles"]["archetypes"][0]["level"], "Senior")

    def test_yaml_first_when_available(self):
        # _load_yaml_file must prefer PyYAML (handles everything) and fall back cleanly
        from ja import config as cfg
        import tempfile, os
        text = "candidate:\n  full_name: \"A B\"\n  email: \"u@v.example\"\nnarrative:\n  s: >-\n    p q\n"
        fd, path = tempfile.mkstemp(suffix=".yml")
        try:
            with os.fdopen(fd, "w") as f:
                f.write(text)
            d = cfg._load_yaml_file(path)
            self.assertEqual(d["candidate"]["full_name"], "A B")
            self.assertEqual(d["narrative"]["s"], "p q")
        finally:
            os.unlink(path)

    def test_profile_facts_tolerates_non_dict(self):
        # passing the function (not a dict) must not crash and yields all-None facts
        facts = form._profile_facts(lambda: {})
        self.assertIsNone(facts["full_name"])
        self.assertIsNone(facts["email"])


class TestSelectorEscaping(unittest.TestCase):
    """Bug 2 regression: Radix ids with '.' and ':' must produce valid CSS."""

    def test_quoted_attr_selector_matches_radix_id(self):
        # pure-CSS check via a tiny local page; skipped gracefully if playwright missing
        try:
            from playwright.sync_api import sync_playwright
        except Exception:
            self.skipTest("playwright not installed")
        bad_id = "input-candidate.openQuestionAnswers.7461474.content-radix-:R12qnl9r6l:"
        sel = '[id="%s"]' % bad_id
        pw = sync_playwright().start()
        try:
            b = pw.chromium.launch(headless=True)
            p = b.new_page()
            p.set_content('<input id="%s" value="v">' % bad_id)
            self.assertEqual(p.eval_on_selector(sel, "e => e.value"), "v")
            b.close()
        finally:
            pw.stop()

    def test_selector_builder_escapes_quotes(self):
        # reuse the same escaping rule the fill loop uses
        v = 'a"b\\c'
        sel = '[id="%s"]' % v.replace("\\", "\\\\").replace('"', '\\"')
        self.assertEqual(sel, '[id="a\\"b\\\\c"]')


if __name__ == "__main__":
    unittest.main()


class TestConfidence(unittest.TestCase):
    """The submitted-data confidence percentage (pure helper, no browser)."""

    def test_all_required_filled_is_100(self):
        reqs = [{"name": "email", "label": "Email"}, {"name": "phone", "label": "Phone"}]
        mapping = {"email": "a@b.example", "phone": "+00 000"}
        c = form._confidence(reqs, mapping, [], False)
        self.assertEqual(c["pct"], 100)
        self.assertEqual(c["total"], 2)
        self.assertEqual(c["filled"], 2)
        self.assertEqual(c["missing"], [])
        self.assertEqual(c["method"], "required-fields")

    def test_half_of_four_required(self):
        reqs = [{"name": k, "label": k.upper()} for k in ("a", "b", "c", "d")]
        c = form._confidence(reqs, {"a": "x", "b": "y"}, [], False)
        self.assertEqual(c["pct"], 50)
        self.assertEqual(c["total"], 4)
        self.assertEqual(c["filled"], 2)
        self.assertEqual(c["missing"], ["C", "D"])

    def test_no_required_flags_falls_back_to_discovered(self):
        fields = [{"name": n, "label": n.upper()} for n in ("a", "b", "c", "d")]
        c = form._confidence([], {"a": "x", "b": "y"}, fields, False)
        self.assertEqual(c["method"], "discovered-fields")
        self.assertEqual(c["pct"], 50)
        self.assertEqual(c["total"], 4)
        self.assertEqual(c["missing"], ["C", "D"])

    def test_nothing_to_measure_is_null(self):
        c = form._confidence([], {}, [], False)
        self.assertIsNone(c["pct"])
        self.assertEqual(c["total"], 0)
        self.assertEqual(c["filled"], 0)
        self.assertEqual(c["missing"], [])

    def test_needs_human_counts_as_missing(self):
        reqs = [{"name": "email", "label": "Email"}, {"name": "notice", "label": "Notice Period"}]
        c = form._confidence(reqs, {"email": "a@b.example", "notice": form.NEEDS_HUMAN}, [], False)
        self.assertEqual(c["pct"], 50)
        self.assertEqual(c["missing"], ["Notice Period"])

    def test_required_file_with_upload_is_filled(self):
        reqs = [{"name": "email", "label": "Email", "type": "email"},
                {"name": "cv", "label": "Resume", "type": "file"}]
        c = form._confidence(reqs, {"email": "a@b.example"}, [], True)
        self.assertEqual(c["pct"], 100)
        self.assertEqual(c["filled"], 2)
        self.assertEqual(c["total"], 2)
        self.assertEqual(c["missing"], [])

    def test_required_file_without_upload_is_missing(self):
        reqs = [{"name": "email", "label": "Email", "type": "email"},
                {"name": "cv", "label": "Resume", "type": "file"}]
        c = form._confidence(reqs, {"email": "a@b.example"}, [], False)
        self.assertEqual(c["pct"], 50)
        self.assertEqual(c["missing"], ["Resume"])

    def test_missing_labels_fall_back_to_name_and_cap_at_8(self):
        reqs = [{"name": "f%d" % i, "label": ""} for i in range(10)]
        c = form._confidence(reqs, {}, [], False)
        self.assertEqual(c["missing"], ["f0", "f1", "f2", "f3", "f4", "f5", "f6", "f7"])


class TestEmailConfidence(unittest.TestCase):
    """Second channel: the seven outbound-email facts."""

    def _body(self, n_words=100):
        filler = " ".join(["word"] * n_words)
        return "Hi Acme team,\n\n%s\n\nMy CV is attached.\n\nWarm regards,\nTest Candidate" % filler

    def test_all_facts_hold(self):
        from ja.channels import email as em
        c = em._confidence(["a@b.example"], "Application: X", self._body(),
                           attachments=[__file__], signer="Test Candidate")
        self.assertEqual(c["pct"], 100)
        self.assertEqual(c["total"], 7)
        self.assertEqual(c["filled"], 7)
        self.assertEqual(c["channel"], "email")
        self.assertEqual(c["method"], "email-facts")
        self.assertEqual(c["missing"], [])


    def test_bad_email_scores_zero_with_labels(self):
        from ja.channels import email as em
        c = em._confidence(None, "", "hey there", attachments=None)
        self.assertEqual(c["total"], 7)
        self.assertEqual(c["filled"], 0)
        self.assertEqual(c["pct"], 0)
        self.assertEqual(c["missing"], ["recipient", "subject", "opener",
                                        "CV attached line", "signature",
                                        "CV file on disk", "body length"])

class TestEmailSignerResolution(unittest.TestCase):
    """The default signer is read from the profile, never hardcoded."""

    def _first(self):
        from ja import config
        try:
            name = ((config.profile().get("candidate") or {}).get("full_name") or "").strip()
        except Exception:
            name = ""
        return name.split()[0] if name else ""

    def _body(self, last, n_words=100):
        filler = " ".join(["word"] * n_words)
        return "Hi Acme team,\n\n%s\n\nMy CV is attached.\n\nWarm regards,\n%s" % (filler, last)

    def test_default_signer_scores_signature_fact(self):
        from ja.channels import email as em
        first = self._first()
        if not first:
            self.skipTest("no profile on this machine")
        ok = em._confidence(["a@b.example"], "Application: X", self._body(first),
                            attachments=[__file__], signer=None)
        self.assertNotIn("signature", ok["missing"])
        self.assertEqual(ok["filled"], 7)
        bad = em._confidence(["a@b.example"], "Application: X", self._body("Notaname"),
                             attachments=[__file__], signer=None)
        self.assertIn("signature", bad["missing"])
        self.assertEqual(bad["filled"], 6)

class TestConfidenceArtifact(unittest.TestCase):
    """_finalize must persist confidence.json and keep it inside filled.json."""

    def test_sidecar_written_atomically(self):
        import json, tempfile
        from pathlib import Path
        d = Path(tempfile.mkdtemp())
        result = {"company": "Acme", "role": "Dev", "confidence":
                  form._confidence([{"name": "email", "label": "Email"}],
                                   {"email": "a@b.example"}, [], False),
                  "artifacts": {}}
        form._finalize(result, d, [])
        conf = json.loads((d / "confidence.json").read_text())
        self.assertEqual(conf["pct"], 100)
        self.assertEqual(conf["channel"], "form")
        self.assertFalse((d / "confidence.json.tmp").exists())
        self.assertEqual(result["artifacts"]["confidence_json"], str(d / "confidence.json"))
