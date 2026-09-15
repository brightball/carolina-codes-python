#!/usr/bin/env python3
"""Structural checks for pre-commit hooks and parallel Gitea jobs."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
WORKFLOW = ROOT / ".gitea" / "workflows" / "precommit.yml"
PRECOMMIT = ROOT / ".pre-commit-config.yaml"

CLONE_CMD = (
    "git clone --depth 1 --no-checkout "
    '"https://x-access-token:${token}@${host}/${GITHUB_REPOSITORY}" .'
)

CHECK_JOBS = ("tests", "sast", "pip-audit", "gitleaks", "lint")


def _job_bodies(text: str) -> dict[str, str]:
    jobs: dict[str, str] = {}
    matches = list(re.finditer(r"^  ([A-Za-z0-9_-]+):\n", text, re.M))
    jobs_idx = text.find("\njobs:\n")
    if jobs_idx < 0:
        return jobs
    for i, match in enumerate(matches):
        if match.start() < jobs_idx:
            continue
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        jobs[match.group(1)] = text[start:end]
    return jobs


class QualityGateLayoutTests(unittest.TestCase):
    def test_precommit_has_five_named_checks(self):
        text = PRECOMMIT.read_text()
        for hook_id in ("tests", "bandit", "pip-audit", "gitleaks", "ruff-check"):
            self.assertRegex(
                text,
                rf"(?m)^\s+- id: {re.escape(hook_id)}\s*$",
                f"pre-commit must include hook id {hook_id}",
            )
        self.assertIn("ruff-format", text)
        self.assertIn("repo: https://github.com/gitleaks/gitleaks", text)
        self.assertIn("repo: https://github.com/astral-sh/ruff-pre-commit", text)
        self.assertIn("repo: https://github.com/PyCQA/bandit", text)
        self.assertIn("repo: https://github.com/pypa/pip-audit", text)

    def test_gitea_workflow_is_one_parallel_job_per_check(self):
        self.assertTrue(WORKFLOW.is_file(), "missing .gitea/workflows/precommit.yml")
        text = WORKFLOW.read_text()
        self.assertNotRegex(text, r"(?m)^\s*git init\b")
        self.assertNotIn("git config --global init.defaultBranch", text)
        self.assertNotIn("actions/checkout", text)
        self.assertRegex(
            text,
            r"GITHUB_TOKEN:\s*\$\{\{\s*github\.token\s*\}\}",
            "job token must be passed so container git fetch can auth",
        )

        jobs = _job_bodies(text)
        self.assertEqual(set(jobs), set(CHECK_JOBS), f"jobs={sorted(jobs)}")
        self.assertNotRegex(text, r"(?m)^\s+needs:")
        self.assertNotRegex(text, r"(?m)^\s+- run: pre-commit run")

        for name, body in jobs.items():
            self.assertIn(CLONE_CMD, body, f"{name} must clone with the job token")
            self.assertIn('git fetch --depth 1 origin "${GITHUB_SHA}"', body)
            self.assertIn("missing job token for git fetch", body)

        self.assertIn("uv run python perf_test.py", jobs["tests"])
        self.assertIn("python -m unittest discover", jobs["tests"])
        self.assertIn("bandit", jobs["sast"])
        self.assertIn("pip-audit", jobs["pip-audit"])
        self.assertIn("gitleaks git", jobs["gitleaks"])
        self.assertIn("ruff check", jobs["lint"])
        self.assertIn("ruff format --check", jobs["lint"])


if __name__ == "__main__":
    unittest.main()
