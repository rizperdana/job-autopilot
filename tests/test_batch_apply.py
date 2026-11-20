"""batch-apply: the submit gate and the batch accounting (pure, no browser).

The gate is the only thing between the candidate's identity and an irreversible
click, so it is tested directly over result dicts, not through a live run.
"""
import unittest
import urllib.error
from ja import batch_apply as ba


def _res(pct, fields=None, **kw):
    r = {"confidence": {"pct": pct, "fields": fields or []}, "needs_human": [], "artifacts": {}}
    r.update(kw)
    return r


class TestSubmitVerdict(unittest.TestCase):
    """pct >= 90 is necessary, never sufficient."""

    def test_high_confidence_and_clean_is_allowed(self):
        allowed, reason = ba.submit_verdict(_res(95))
        self.assertTrue(allowed)
        self.assertIn("95", reason)

    def test_high_confidence_with_a_blank_required_field_is_refused(self):
        allowed, reason = ba.submit_verdict(
            _res(95, submit_blocked_reason="missing required: Notice Period"))
        self.assertFalse(allowed)
        self.assertIn("missing required", reason)

    def test_below_the_threshold_is_refused(self):
        allowed, reason = ba.submit_verdict(_res(89))
        self.assertFalse(allowed)
        self.assertIn("< 90", reason)

    def test_no_measurable_confidence_is_refused(self):
        allowed, reason = ba.submit_verdict(_res(None))
        self.assertFalse(allowed)
        self.assertIn("no measurable confidence", reason)

    def test_captcha_is_refused(self):
        allowed, reason = ba.submit_verdict(_res(99, needs_human_captcha=True))
        self.assertFalse(allowed)
        self.assertIn("captcha", reason)

    def test_unsolved_turnstile_is_refused(self):
        self.assertFalse(ba.submit_verdict(_res(99, turnstile={"ok": False, "error": "timeout"}))[0])
        self.assertTrue(ba.submit_verdict(_res(99, turnstile={"ok": True, "len": 700}))[0])

    def test_password_field_is_refused(self):
        allowed, reason = ba.submit_verdict(_res(99, password_fields=["password"]))
        self.assertFalse(allowed)
        self.assertIn("login/auth wall", reason)

    def test_stopped_run_is_refused(self):
        self.assertFalse(ba.submit_verdict(_res(99, stopped="dead"))[0])

    def test_junk_result_is_refused(self):
        self.assertFalse(ba.submit_verdict(None)[0])
        self.assertFalse(ba.submit_verdict({})[0])

    def test_required_field_needing_a_human_is_refused(self):
        fields = [{"name": "question_1", "required": True, "state": "filled"}]
        r = _res(99, fields, needs_human=[{"field": "question_1", "error": "fill timed out"}])
        allowed, reason = ba.submit_verdict(r)
        self.assertFalse(allowed)
        self.assertIn("question_1", reason)

    def test_uploaded_file_is_not_a_missing_required_field(self):
        # file inputs always land in needs_human, even when the upload worked
        fields = [{"name": "resume", "required": True, "state": "uploaded"}]
        r = _res(95, fields, needs_human=[{"field": "resume", "label": "Attach"}])
        self.assertTrue(ba.submit_verdict(r)[0])

    def test_is_wall_only_covers_page_walls(self):
        self.assertTrue(ba.is_wall(_res(10, needs_human_captcha=True)))
        self.assertTrue(ba.is_wall(_res(10, password_fields=["password"])))
        self.assertFalse(ba.is_wall(_res(10, submit_blocked_reason="missing required: X")))


class TestCap(unittest.TestCase):
    """The committed default is 0 (refuse everything); a run overrides per run."""

    def test_config_default_when_no_override(self):
        self.assertEqual(ba.resolve_cap(None, 0), (0, False))
        self.assertEqual(ba.resolve_cap(None, 7), (7, False))

    def test_override_wins_and_is_marked(self):
        self.assertEqual(ba.resolve_cap(50, 0), (50, True))
        self.assertEqual(ba.resolve_cap(10, 10), (10, False))


class TestSelection(unittest.TestCase):
    """Selection guards. Both bugs here were caught by a dry run, not by review."""

    MARKETING = {"job_id": "m1", "company": "Launch Potato",
                 "role": "Head of Paid Social, Insurance", "decision": "apply",
                 "score": 90, "channel": "form", "url": "https://x/apply",
                 "apply_url": "https://x/apply"}

    def test_a_marketing_role_is_never_a_candidate(self):
        entry = {k: self.MARKETING[k] for k in
                 ("job_id", "company", "role", "decision", "score", "channel", "url")}
        cands, tally = ba.select_candidates([entry], {"m1": self.MARKETING}, 5, ("apply",))
        self.assertEqual(cands, [])
        self.assertEqual(tally.get("not an engineering role (rank 3)"), 1)

    def test_a_rank3_role_reaching_the_runner_asserts(self):
        ba._engineering_rank({"role": "Senior Software Engineer"})  # rank 2
        with self.assertRaises(AssertionError):
            ba._engineering_rank({"role": "Head of Paid Social, Insurance"})

    def test_a_stored_decision_is_re_evaluated_and_drift_counted(self):
        # stored as apply, but the gates score this record nowhere near apply
        entry = {"job_id": "s1", "company": "Acme", "role": "Senior Software Engineer",
                 "decision": "apply", "score": 99, "channel": "form",
                 "url": "https://job-boards.greenhouse.io/acme/jobs/1"}
        pool = {"s1": dict(entry, apply_url=entry["url"], ats="greenhouse")}
        cands, tally = ba.select_candidates([entry], pool, 5, ("apply",))
        self.assertEqual(cands, [])
        self.assertTrue([k for k in tally if k.startswith("stale decision: stored apply, now ")],
                        "drift must be visible in the tally: %r" % (tally,))

    def test_a_freshly_gated_record_is_a_candidate_whatever_it_was_stored_as(self):
        entry = {"job_id": "f1", "company": "Acme", "role": "Software Engineer",
                 "decision": "review", "channel": "form",
                 "url": "https://job-boards.greenhouse.io/acme/jobs/2"}
        pool = {"f1": dict(entry, apply_url=entry["url"], ats="greenhouse")}
        job = dict(entry)
        job.update(pool["f1"])
        fresh = ba.gates.evaluate(job)["decision"]
        cands, tally = ba.select_candidates([entry], pool, 5, (fresh,))
        self.assertEqual([j["job_id"] for j in cands], ["f1"])
        self.assertEqual(cands[0]["stored_decision"], "review")


class TestLiveness(unittest.TestCase):
    """One GET before any browser starts. Dead is 404/410 only, never a guess."""

    def _opener(self, code=None, exc=None):
        def op(req, timeout=None):
            if exc is not None:
                raise exc
            if code:
                raise urllib.error.HTTPError(req.full_url, code, "x", {}, None)

            class R:
                status = 200
            return R()
        return op

    def test_200_is_alive(self):
        alive, why, status = ba.liveness("https://x/apply", opener=self._opener())
        self.assertTrue(alive)
        self.assertEqual(status, 200)

    def test_404_and_410_are_dead_postings(self):
        for code in (404, 410):
            alive, why, status = ba.liveness("https://x/apply", opener=self._opener(code=code))
            self.assertFalse(alive, code)
            self.assertEqual(status, code)
            self.assertIn("dead posting (http %d)" % code, why)

    def test_403_is_a_wall_not_a_dead_posting(self):
        alive, why, _ = ba.liveness("https://x/apply", opener=self._opener(code=403))
        self.assertFalse(alive)
        self.assertIn("not a dead posting", why)

    def test_a_transient_error_keeps_the_candidate(self):
        # a DNS blip or a 500 must never drop a job that might be alive
        for exc in (urllib.error.URLError("dns"), TimeoutError("slow")):
            self.assertTrue(ba.liveness("https://x/apply", opener=self._opener(exc=exc))[0])
        self.assertTrue(ba.liveness("https://x/apply", opener=self._opener(code=503))[0])

    def test_the_probe_caches_one_get_per_url(self):
        calls = []
        real = ba.liveness

        def fake(url, timeout=None):
            calls.append(url)
            return True, "http 200", 200

        ba.liveness = fake
        try:
            probe = ba._liveness_probe()
            probe("https://x/apply")
            probe("https://x/apply")
            probe("https://y/apply")
        finally:
            ba.liveness = real
        self.assertEqual(calls, ["https://x/apply", "https://y/apply"])

    def test_selection_drops_a_dead_url_and_counts_it(self):
        entry = {"job_id": "d1", "company": "Andela", "role": "Software Engineer",
                 "decision": "apply", "channel": "form",
                 "url": "https://jobs.ashbyhq.com/andela/dead/application"}
        pool = {"d1": dict(entry, apply_url=entry["url"], ats="ashby")}
        job = dict(entry)
        job.update(pool["d1"])
        fresh = ba.gates.evaluate(job)["decision"]
        probe = lambda url: (False, "dead posting (http 404)", 404)  # noqa: E731
        cands, tally = ba.select_candidates([entry], pool, 5, (fresh,), liveness_probe=probe)
        self.assertEqual(cands, [])
        self.assertEqual(tally.get("dead posting (http 404)"), 1)


class TestConfirmation(unittest.TestCase):
    def test_same_url_and_no_signals_is_unconfirmed(self):
        sig = ba.confirmation_signals({"apply_url": "https://x/apply",
                                       "confirmation": {"url": "https://x/apply", "signals": []}})
        self.assertFalse(sig["confirmed"])

    def test_no_confirmation_block_at_all_is_unconfirmed(self):
        self.assertFalse(ba.confirmation_signals({"apply_url": "https://x/apply"})["confirmed"])

    def test_signals_or_a_moved_url_confirm(self):
        self.assertTrue(ba.confirmation_signals(
            {"apply_url": "https://x/apply",
             "confirmation": {"url": "https://x/apply", "signals": ["thank"]}})["confirmed"])
        self.assertTrue(ba.confirmation_signals(
            {"apply_url": "https://x/apply",
             "confirmation": {"url": "https://x/thanks", "signals": []}})["confirmed"])


if __name__ == "__main__":
    unittest.main()
