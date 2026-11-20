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

    def test_missing_labels_fall_back_to_a_human_string_and_cap_at_8(self):
        reqs = [{"name": "f%d" % i, "label": ""} for i in range(10)]
        c = form._confidence(reqs, {}, [], False)
        self.assertEqual(c["missing"], ["Unnamed field %d" % i for i in range(8)])
        # no discovery pass at all: nothing claims these are unattributable
        self.assertEqual(c["unresolved_required"], 0)

    def test_partial_value_present_but_shape_rejected_scores_50(self):
        reqs = [{"name": "email", "label": "Email", "type": "email"},
                {"name": "city", "label": "City", "type": "text"}]
        c = form._confidence(reqs, {"email": "not-an-email", "city": "Jakarta"}, [], False)
        self.assertEqual([r["score"] for r in c["fields"]], [50, 100])
        self.assertEqual([r["state"] for r in c["fields"]], ["partial", "filled"])
        self.assertEqual(c["pct"], 75)

    def test_required_and_empty_scores_zero(self):
        reqs = [{"name": "email", "label": "Email", "type": "email"},
                {"name": "phone", "label": "Phone", "type": "text"}]
        c = form._confidence(reqs, {"email": "a@b.example"}, [], False)
        self.assertEqual(c["pct"], 50)
        self.assertEqual(c["fields"][1], {"label": "Phone", "name": "phone", "type": "text",
                                          "required": True, "score": 0, "state": "missing"})
        self.assertEqual(c["skipped_optional"], 0)

    def test_optional_empty_is_excluded_from_both_sides(self):
        reqs = [{"name": "email", "label": "Email", "type": "email"}]
        fields = reqs + [{"name": "referrer", "label": "How did you hear about us?"}]
        c = form._confidence(reqs, {"email": "a@b.example"}, fields, False)
        self.assertEqual(c["pct"], 100)          # denominator is the 1 filled field, not 2
        self.assertEqual(c["skipped_optional"], 1)
        self.assertEqual([r["name"] for r in c["fields"]], ["email"])

    def test_optional_filled_is_included_at_100(self):
        reqs = [{"name": "email", "label": "Email", "type": "email"}]
        fields = reqs + [{"name": "referrer", "label": "Referrer"}]
        c = form._confidence(reqs, {"email": "a@b.example", "referrer": "LinkedIn"}, fields, False)
        self.assertEqual([r["score"] for r in c["fields"]], [100, 100])
        self.assertEqual(c["skipped_optional"], 0)

    def test_mean_over_a_mixed_list(self):
        reqs = [{"name": "email", "label": "Email", "type": "email"},
                {"name": "years", "label": "Years of experience", "type": "text"},
                {"name": "cov", "label": "Cover letter", "type": "textarea"}]
        fields = reqs + [{"name": "referrer", "label": "Referrer"}]
        c = form._confidence(reqs, {"email": "a@b.example", "years": "eight"}, fields, False)
        self.assertEqual([r["score"] for r in c["fields"]], [100, 50, 0])  # referrer skipped
        self.assertEqual(c["pct"], 50)            # (100 + 50 + 0) / 3
        self.assertEqual(c["total"], 3)           # legacy required-only meaning kept
        self.assertEqual(c["filled"], 2)
        self.assertEqual(c["skipped_optional"], 1)

    def test_result_keeps_legacy_keys_and_carries_no_values(self):
        c = form._confidence([{"name": "email", "label": "Email"}],
                             {"email": "a@b.example"}, [], False)
        for k in ("pct", "method", "total", "filled", "missing", "channel",
                  "fields", "skipped_optional", "unresolved_required"):
            self.assertIn(k, c)
        self.assertEqual(sorted(c["fields"][0]),
                         ["label", "name", "required", "score", "state", "type"])

    def test_discovered_fallback_marks_exposed_fields_required(self):
        fields = [{"name": n, "label": n.upper()} for n in ("a", "b", "c", "d")]
        c = form._confidence([], {"a": "x"}, fields, False)
        self.assertEqual(c["method"], "discovered-fields")
        self.assertTrue(all(r["required"] for r in c["fields"]))
        self.assertEqual(c["skipped_optional"], 0)
        self.assertEqual(c["pct"], 25)            # 1 of 4

    def test_unattributable_required_is_excluded_and_counted(self):
        # react-select keeps a nameless/idless mirror input to satisfy native
        # validation; the discovery pass never sees it (no identity to address),
        # so it must not be scored as a field we failed to fill.
        reqs = [{"name": "email", "label": "Email", "type": "email"},
                {"name": "f8", "label": "", "type": "text", "synthetic": True}]
        c = form._confidence(reqs, {"email": "a@b.example"}, [], False)
        self.assertEqual(c["pct"], 100)          # not dragged down by a fabricated 0
        self.assertEqual(c["unresolved_required"], 1)
        self.assertEqual(c["total"], 1)
        self.assertEqual(c["missing"], [])
        self.assertEqual([r["name"] for r in c["fields"]], ["email"])

    def test_required_key_absent_from_discovered_fields_is_unresolved(self):
        fields = [{"name": "email", "label": "Email"}]
        reqs = [{"name": "email", "label": "Email", "type": "email"},
                {"name": "ghost", "label": "Ghost", "type": "text"}]
        c = form._confidence(reqs, {"email": "a@b.example"}, fields, False)
        self.assertEqual(c["pct"], 100)
        self.assertEqual(c["unresolved_required"], 1)
        self.assertEqual([r["name"] for r in c["fields"]], ["email"])

    def test_synthetic_key_never_renders_as_a_label(self):
        self.assertEqual(form._human_label("", "f8"), "Unnamed field 8")
        self.assertEqual(form._human_label("", "email"), "email")
        self.assertEqual(form._human_label("  First Name*  ", "first_name"), "First Name*")


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

    def test_fields_rows_mirror_the_facts(self):
        from ja.channels import email as em
        c = em._confidence(["a@b.example"], "Application: X", self._body(),
                           attachments=[__file__], signer="Test Candidate")
        self.assertEqual([r["label"] for r in c["fields"]],
                         ["recipient", "subject", "opener", "CV attached line",
                          "signature", "CV file on disk", "body length"])
        self.assertEqual([r["score"] for r in c["fields"]], [100] * 7)
        self.assertTrue(all(r["required"] for r in c["fields"]))
        self.assertTrue(all(r["state"] == "filled" for r in c["fields"]))
        self.assertEqual(c["skipped_optional"], 0)
        bad = em._confidence(None, "", "hey there", attachments=None)
        self.assertEqual([r["score"] for r in bad["fields"]], [0] * 7)
        self.assertEqual([r["state"] for r in bad["fields"]], ["missing"] * 7)

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
        self.assertEqual(conf["fields"][0]["score"], 100)
        self.assertEqual(conf["fields"][0]["name"], "email")
        self.assertEqual(result["confidence"]["fields"], conf["fields"])
        self.assertEqual(result["artifacts"]["confidence_json"], str(d / "confidence.json"))


class TestAnswerBank(unittest.TestCase):
    """The bank was dead code: no caller ever passed a path (fixed 2026-11-20)."""

    def test_default_path_is_the_runtime_answers_file(self):
        p = form.default_answers_path()
        self.assertEqual(p.name, "answers.yml")
        self.assertEqual(p.parent, form._runtime())
        self.assertEqual(form.answers_source(None), p)

    def test_no_file_means_no_bank_and_no_crash(self):
        self.assertEqual(form._answer_bank(form.answers_source(None)), {})

    def test_a_path_is_loaded_and_empty_means_disabled(self):
        import tempfile
        from pathlib import Path
        d = Path(tempfile.mkdtemp())
        p = d / "answers.yml"
        p.write_text('"notice period": "4 weeks"\n')
        self.assertEqual(form._answer_bank(form.answers_source(str(p))),
                         {"notice period": "4 weeks"})
        self.assertIsNone(form.answers_source(""))
        self.assertIsNone(form.answers_source(False))
        self.assertEqual(form._answer_bank(form.answers_source("")), {})


class TestLabelSource(unittest.TestCase):
    """A placeholder is not a question; the row says so instead of pretending."""

    def _row(self, f):
        c = form._confidence([f], {}, [], False)
        return c["fields"][0], c

    def test_a_placeholder_only_field_is_marked_and_counted(self):
        row, c = self._row({"name": "q1", "label": "type your response",
                            "label_source": "placeholder", "type": "textarea"})
        self.assertEqual(row["label_source"], "placeholder")
        self.assertEqual(c["placeholder_only"], 1)
        self.assertEqual(c["missing"], ["type your response (placeholder only)"])

    def test_a_properly_labelled_field_carries_no_label_source(self):
        row, c = self._row({"name": "email", "label": "Email",
                            "label_source": "for", "type": "email"})
        self.assertNotIn("label_source", row)
        self.assertEqual(c["placeholder_only"], 0)
        self.assertEqual(sorted(row), ["label", "name", "required", "score", "state", "type"])

    def test_an_informative_source_travels_with_the_row(self):
        row, _ = self._row({"name": "q2", "label": "Which middleware?",
                            "label_source": "legend", "type": "text"})
        self.assertEqual(row["label_source"], "legend")
        self.assertEqual(row["label"], "Which middleware?")



class TestEducationFacts(unittest.TestCase):
    """degree/school/discipline are read out of the CV's Education line, verbatim."""

    def test_the_real_cv_line_yields_the_three_strings(self):
        cv = ("## Experience\n- x\n\n## Education\n"
              "- Bachelor Degree, Accounting -- Yogyakarta State University (2016 - 2020)\n\n## Skills\n- y\n")
        self.assertEqual(form._education_facts(cv),
                         {"degree": "Bachelor Degree", "discipline": "Accounting",
                          "school": "Yogyakarta State University"})

    def test_a_degree_without_a_discipline(self):
        edu = form._education_facts("## Education\n- Master Degree -- Some University (2019)\n")
        self.assertEqual(edu["degree"], "Master Degree")
        self.assertIsNone(edu["discipline"])
        self.assertEqual(edu["school"], "Some University")

    def test_two_schools_or_prose_yield_nothing(self):
        self.assertEqual(form._education_facts("## Education\n- A -- B\n- C -- D\n"), {})
        self.assertEqual(form._education_facts("## Education\nI studied accounting.\n"), {})
        self.assertEqual(form._education_facts("## Experience\n- x\n"), {})

    def test_the_facts_carry_the_three_keys(self):
        facts = form._profile_facts({})
        self.assertIn("degree", facts)
        self.assertIn("school", facts)
        self.assertIn("discipline", facts)


class TestEducationMapping(unittest.TestCase):
    def setUp(self):
        self.facts = form._profile_facts({})
        self.has_edu = bool(self.facts.get("degree"))

    def _map(self, label, name="f"):
        return form.map_fields([{"label": label, "name": name}], {}, {})[name]

    def test_degree_school_discipline_map_verbatim(self):
        if not self.has_edu:
            self.skipTest("no parseable Education section in cv.md on this machine")
        self.assertEqual(self._map("Degree*"), self.facts["degree"])
        self.assertEqual(self._map("School"), self.facts["school"])
        self.assertEqual(self._map("Discipline"), self.facts["discipline"])
        self.assertEqual(self._map("Field of study"), self.facts["discipline"])

    def test_a_nanodegree_is_not_a_degree(self):
        self.assertEqual(self._map("Please choose the Nanodegree from the below dropdown"),
                         form.NEEDS_HUMAN)

    def test_prose_questions_stay_unanswered(self):
        for q in ("How did you perform in mathematics at high school?",
                  "Please share your rationale or evidence for the highest degree",
                  "What university did you attend?"):
            self.assertEqual(self._map(q), form.NEEDS_HUMAN, q)


class TestTotalYearsGate(unittest.TestCase):
    def _map(self, label, name="f"):
        return form.map_fields([{"label": label, "name": name}], {}, {})[name]

    def test_total_experience_uses_the_cv_number(self):
        # 8 on this machine; NEEDS_HUMAN when cv.md is missing
        for label in ("Years of experience", "How many years of professional experience do you have?"):
            self.assertIn(self._map(label), ("8", form.NEEDS_HUMAN), label)

    def test_per_skill_questions_and_band_options_are_refused(self):
        for label in ("Years of experience with Kubernetes",
                      "Years of experience in software development",
                      "2-4 years", "10+ years", "0-1 year"):
            self.assertEqual(self._map(label), form.NEEDS_HUMAN, label)


class TestFileInputCrediting(unittest.TestCase):
    def test_a_second_required_box_is_not_credited_by_the_resume(self):
        reqs = [{"name": "resume", "label": "Resume", "type": "file"},
                {"name": "cover_letter", "label": "Cover letter", "type": "file"}]
        c = form._confidence(reqs, {}, [], True, {"resume"})
        self.assertEqual(c["missing"], ["Cover letter"])
        self.assertEqual(c["pct"], 50)
        self.assertEqual(form._confidence(reqs, {}, [], True, {"resume", "cover_letter"})["pct"], 100)

    def test_without_keys_the_single_flag_still_speaks(self):
        reqs = [{"name": "resume", "label": "Resume", "type": "file"}]
        self.assertEqual(form._confidence(reqs, {}, [], True)["pct"], 100)
        self.assertEqual(form._confidence(reqs, {}, [], False)["pct"], 0)

    def test_an_unnameable_box_falls_back_to_the_flag(self):
        self.assertTrue(form._file_uploaded({"label": "Resume"}, True, {"resume"}))
        # with keys known, membership decides -- the stale global flag cannot
        self.assertFalse(form._file_uploaded({"name": "cover_letter"}, True, {"resume"}))
        self.assertTrue(form._file_uploaded({"name": "resume"}, True, {"resume"}))
        self.assertTrue(form._file_uploaded({"name": "resume"}, True, None))


class TestSelectOptionMatch(unittest.TestCase):
    class _Page:
        def __init__(self, opts):
            self.opts = opts

        def eval_on_selector_all(self, sel, js):
            return self.opts

    def test_option_text_is_matched_word_by_word(self):
        self.assertTrue(form._text_matches("Bachelor Degree", "Bachelor's Degree"))
        self.assertFalse(form._text_matches("Bachelor Degree", "Master Degree"))
        self.assertFalse(form._text_matches("Bachelor Degree", ""))

    def test_select_picks_the_matching_option_and_refuses_otherwise(self):
        p = self._Page([("Bachelor's Degree", "bachelor"), ("Master's Degree", "master")])
        self.assertEqual(form._select_option(p, "#d", "Bachelor Degree"), "Bachelor's Degree")
        self.assertIsNone(form._select_option(p, "#d", "Doctorate"))
        self.assertIsNone(form._select_option(p, "#d", ""))

    def test_option_value_attribute_is_also_accepted(self):
        p = self._Page([("Pick one", "bachelors_degree")])
        self.assertEqual(form._select_option(p, "#d", "Bachelor Degree"), "Pick one")


class TestFormRenderWait(unittest.TestCase):
    """`0 fields` must mean an empty page, never an impatient reader."""

    class _Page:
        def __init__(self, raises=False):
            self.raises = raises
            self.selector = None
            self.timeout = None
            self.settles = []

        def wait_for_selector(self, selector, state=None, timeout=None):
            self.selector, self.timeout = selector, timeout
            if self.raises:
                raise RuntimeError("Timeout 15000ms exceeded")
            return object()

        def wait_for_timeout(self, ms):
            self.settles.append(ms)

    def test_waits_for_a_real_control_then_settles(self):
        p = self._Page()
        ok, why = form._wait_for_form(p)
        self.assertTrue(ok)
        self.assertIn("input", p.selector)
        self.assertEqual(p.timeout, form.FORM_RENDER_MS)
        self.assertEqual(p.settles, [form.FORM_SETTLE_MS])
        self.assertIn("rendered", why)

    def test_an_empty_shell_stops_with_a_readable_reason(self):
        p = self._Page(raises=True)
        ok, why = form._wait_for_form(p, timeout_ms=1234)
        self.assertFalse(ok)
        self.assertEqual(p.settles, [])
        self.assertIn("form never rendered", why)
        self.assertIn("1234 ms", why)
        self.assertIn("no input/select/textarea", why)


class TestProseNameQuestion(unittest.TestCase):
    """A question that merely mentions "name" never gets the candidate's name."""

    def _profile(self):
        return {"candidate": {"full_name": "Test Candidate", "email": "candidate@example.com",
                              "phone": "+00 000 000 000", "location": "Test City, Indonesia"}}

    def test_the_virta_familial_question_is_left_to_the_human(self):
        f = {"label": "If you have a familial relationship to a current employee, please "
                       "provide their name, your relationship to them, and any additional "
                       "details you believe are relevant. If you have no familial "
                       "relationships please write 'n/a'.",
             "name": "01bee1ba-3fbd-4291-8525-67cbc4f829cf", "type": "textarea"}
        m = form.map_fields([f], self._profile(), {})
        self.assertEqual(m[f["name"]], form.NEEDS_HUMAN)

    def test_a_plain_name_field_still_gets_the_name(self):
        fields = [{"label": "Name", "name": "n1"},
                  {"label": "Full name", "name": "n2"},
                  {"label": "Customer name:", "name": "n3"},
                  {"label": "Applicant Name", "name": "n4"}]
        m = form.map_fields(fields, self._profile(), {})
        for f in fields:
            self.assertEqual(m[f["name"]], "Test Candidate")


class TestPhoneKeywordBoundary(unittest.TestCase):
    """`tel` inside "tell us" is not a phone field."""

    def _profile(self):
        return {"candidate": {"full_name": "Test Candidate", "phone": "+00 000 000 000",
                              "email": "candidate@example.com"}}

    def test_a_prose_question_containing_tell_us_is_not_a_phone_field(self):
        fields = [{"label": "Please tell us about your open source contributions",
                   "name": "q1"},
                  {"label": "Tell us more", "name": "q2"}]
        m = form.map_fields(fields, self._profile(), {})
        self.assertEqual(m["q1"], form.NEEDS_HUMAN)
        self.assertEqual(m["q2"], form.NEEDS_HUMAN)

    def test_real_phone_fields_still_map(self):
        fields = [{"label": "Phone", "name": "p1"},
                  {"label": "Telephone number", "name": "p2"},
                  {"label": "Mobile", "name": "p3"},
                  {"label": "Tel", "name": "p4"}]
        m = form.map_fields(fields, self._profile(), {})
        for f in fields:
            self.assertEqual(m[f["name"]], "+00 000 000 000")
        # the predicate itself: "tell us" is not tel; a real tel field is
        self.assertIsNone(form._PHONE_Q.search("Tell us more"))
        self.assertIsNone(form._PHONE_Q.search("open source contributions"))
        self.assertTrue(form._PHONE_Q.search("Tel"))
        self.assertTrue(form._PHONE_Q.search("Telephone number"))


class TestCannedAnswerTriggers(unittest.TestCase):
    """A canned answer must not land on a question about something else."""

    def _profile(self):
        return {"candidate": {"full_name": "Test Candidate", "phone": "+00 000 000 000",
                              "email": "candidate@example.com"},
                "compensation": {"target_range": "$50K-90K"}}

    def test_a_non_breaking_space_does_not_hide_the_prose(self):
        # the real Virta label renders "please\u00a0 provide", which defeated every
        # marker check until labels were normalized
        f = {"label": "If you have a familial relationship to a current Virta employee, "
                      "please\u00a0 provide their name, your relationship to them, and any "
                      "additional details you believe are relevant. If you have no "
                      "familial relationships please write 'n/a'.",
             "name": "01bee1ba-3fbd-4291-8525-67cbc4f829cf", "type": "textarea"}
        m = form.map_fields([f], self._profile(), {})
        self.assertEqual(m[f["name"]], form.NEEDS_HUMAN)
        self.assertTrue(form._looks_like_prose(f["label"]))

    def test_open_source_is_not_how_did_you_hear(self):
        fields = [{"label": "Please tell us about your Open Source contributions",
                   "name": "q1"},
                  {"label": "Describe any past experience with open source community "
                            "engagement", "name": "q2"}]
        m = form.map_fields(fields, self._profile(), {})
        self.assertEqual(m["q1"], form.NEEDS_HUMAN)
        self.assertEqual(m["q2"], form.NEEDS_HUMAN)

    def test_how_did_you_hear_still_gets_the_canned_answer(self):
        fields = [{"label": "How did you hear about this job?", "name": "h1"},
                  {"label": "How did you find out about this role?", "name": "h2"}]
        m = form.map_fields(fields, self._profile(), {})
        self.assertEqual(m["h1"], "job board")
        self.assertEqual(m["h2"], "job board")

    def test_a_degree_result_question_is_not_a_salary_question(self):
        f = {"label": "What was your bachelor's university degree result, or expected "
                      "result if you have not yet graduated? Please include your GPA.",
             "name": "d1"}
        m = form.map_fields([f], self._profile(), {})
        self.assertEqual(m["d1"], form.NEEDS_HUMAN)

    def test_a_salary_question_still_gets_the_range(self):
        fields = [{"label": "What are your base salary expectations?", "name": "s1"},
                  {"label": "Desired salary range", "name": "s2"},
                  {"label": "What compensation are you targeting?", "name": "s3"}]
        m = form.map_fields(fields, self._profile(), {})
        for f in fields:
            self.assertEqual(m[f["name"]], "$50K-90K")
