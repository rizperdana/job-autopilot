"""Tests for ja.priority."""
import unittest

from ja.priority import role_rank, pick_per_company


class TestRoleRank(unittest.TestCase):
    CASES = [
        ("Software Engineer", 0),
        ("Backend Engineer", 0),
        ("Full Stack Developer", 0),
        ("ML Engineer", 0),
        ("DevOps Engineer", 1),
        ("Site Reliability Engineer", 1),
        ("Platform Engineer", 1),
        ("Senior DevOps Engineer", 1),
        ("Senior Backend Engineer", 2),
        ("Lead Software Engineer", 2),
        ("Staff Software Engineer", 2),
        ("Principal Engineer", 2),
        ("Engineering Manager", 2),
        ("Marketing Manager", 3),
        ("Product Designer", 3),
    ]

    def test_table(self):
        for title, expected in self.CASES:
            with self.subTest(title=title):
                self.assertEqual(role_rank({"role": title}), expected, title)

    def test_picks_plain_engineer_among_three(self):
        jobs = [
            {"company": "Acme", "role": "Senior Backend Engineer"},
            {"company": "Acme", "role": "DevOps Engineer"},
            {"company": "Acme", "role": "Backend Engineer"},
        ]
        best = pick_per_company(jobs)
        self.assertEqual(best["acme"]["role"], "Backend Engineer")

    def test_company_with_only_senior_still_returned(self):
        jobs = [{"company": "Acme", "role": "Senior Backend Engineer"}]
        best = pick_per_company(jobs)
        self.assertEqual(best["acme"]["role"], "Senior Backend Engineer")


if __name__ == "__main__":
    unittest.main()