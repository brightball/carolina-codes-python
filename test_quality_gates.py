#!/usr/bin/env python3
"""Structural checks for pre-commit hooks and staged Gitea jobs."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
WORKFLOW = ROOT / ".gitea" / "workflows" / "precommit.yml"
PRECOMMIT = ROOT / ".pre-commit-config.yaml"
CI_ARTIFACT = ROOT / "scripts" / "ci_artifact.py"

CLONE_CMD = (
    "git clone --depth 1 --no-checkout "
    '"https://x-access-token:${token}@${host}/${GITHUB_REPOSITORY}" .'
)

CHECK_JOBS = ("tests", "sast", "pip-audit", "gitleaks", "lint")
PREPARE_JOB = "prepare"
APT_CLONE_HINTS = (
    "apt-get update -qq && apt-get install -y --no-install-recommends git ca-certificates curl",
    CLONE_CMD,
)


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

    def test_gitea_workflow_is_prepare_then_parallel_checks(self):
        self.assertTrue(WORKFLOW.is_file(), "missing .gitea/workflows/precommit.yml")
        self.assertTrue(CI_ARTIFACT.is_file(), "missing scripts/ci_artifact.py")
        text = WORKFLOW.read_text()
        self.assertNotRegex(text, r"(?m)^\s*git init\b")
        self.assertNotIn("git config --global init.defaultBranch", text)
        self.assertNotIn("actions/checkout", text)
        self.assertNotIn("actions/upload-artifact@v4", text)
        self.assertNotIn("actions/download-artifact@v4", text)
        self.assertRegex(
            text,
            r"GITHUB_TOKEN:\s*\$\{\{\s*github\.token\s*\}\}",
            "job token must be passed so container git fetch can auth",
        )

        jobs = _job_bodies(text)
        self.assertEqual(
            set(jobs),
            {PREPARE_JOB, *CHECK_JOBS},
            f"jobs={sorted(jobs)}",
        )
        self.assertNotRegex(text, r"(?m)^\s+- run: pre-commit run")

        prep = jobs[PREPARE_JOB]
        self.assertIn(CLONE_CMD, prep)
        self.assertIn('git fetch --depth 1 origin "${GITHUB_SHA}"', prep)
        self.assertIn("missing job token for git fetch", prep)
        self.assertIn("pip install --no-cache-dir uv", prep)
        self.assertIn("uv sync --frozen --group dev", prep)
        self.assertIn("gitleaks", prep)
        self.assertIn("tar -czf", prep)
        self.assertIn("prepared-env", prep)
        self.assertIn("ci_artifact.py upload", prep)
        self.assertNotIn("needs:", prep)
        self.assertNotIn("actions/upload-artifact", prep)

        for name in CHECK_JOBS:
            body = jobs[name]
            self.assertIn(
                "needs: prepare",
                body,
                f"{name} must wait on the prepare job",
            )
            self.assertIn("ci_artifact.py download", body, f"{name} must restore the env")
            self.assertIn("prepared-env", body, f"{name} must restore prepared-env")
            self.assertIn("tar -xzf", body, f"{name} must unpack the prepared env")
            self.assertNotIn("uv sync", body, f"{name} must not repeat uv sync")
            self.assertNotIn("pip install", body, f"{name} must not pip install uv")
            self.assertNotIn("git clone", body, f"{name} must not clone")
            for hint in APT_CLONE_HINTS:
                self.assertNotIn(hint, body, f"{name} must not repeat apt-get+clone")
            self.assertNotIn("apt-get", body, f"{name} must not apt-get")
            for other in CHECK_JOBS:
                self.assertNotRegex(
                    body,
                    rf"(?m)^\s+needs:\s*{re.escape(other)}\s*$",
                    f"{name} must not needs: {other}",
                )

        self.assertIn("uv run python perf_test.py", jobs["tests"])
        self.assertIn("python -m unittest discover", jobs["tests"])
        self.assertIn("bandit", jobs["sast"])
        self.assertIn("pip-audit", jobs["pip-audit"])
        self.assertIn("gitleaks git", jobs["gitleaks"])
        self.assertIn("ruff check", jobs["lint"])
        self.assertIn("ruff format --check", jobs["lint"])

    def test_bandit_and_ruff_cover_the_first_party_tree(self):
        pre = PRECOMMIT.read_text()
        makefile = (ROOT / "Makefile").read_text()
        pyproject = (ROOT / "pyproject.toml").read_text()
        readme = (ROOT / "README.md").read_text()
        jobs = _job_bodies(WORKFLOW.read_text())
        self.assertIn('"-r", "app.py", "scripts"', pre)
        self.assertIn('targets = ["app.py", "scripts"]', pyproject)
        self.assertRegex(makefile, r"(?m)^\s*uv run bandit .*\bscripts\s*$")
        self.assertRegex(jobs["sast"], r"(?m)^[^\n#]*bandit[^\n]*\bscripts\s*$")
        self.assertRegex(makefile, r"(?m)^\s*uv run ruff check \.\s*$")
        self.assertRegex(makefile, r"(?m)^\s*uv run ruff format --check \.\s*$")
        self.assertRegex(jobs["lint"], r"(?m)^\s*- run: uv run ruff check \.\s*$")
        self.assertRegex(jobs["lint"], r"(?m)^\s*- run: uv run ruff format --check \.\s*$")
        self.assertIn("bandit -c pyproject.toml -r app.py scripts", readme)
        self.assertIn("ruff check .", readme)
        self.assertIn("ruff format --check .", readme)

    def test_fly_keeps_a_machine_and_image_uses_the_lockfile(self):
        fly = (ROOT / "fly.toml").read_text()
        self.assertIn("auto_start_machines = true", fly)
        self.assertIn("internal_port = 8080", fly)
        self.assertIn('method = "GET"', fly)
        self.assertIn('path = "/health"', fly)
        match = re.search(r"(?m)^\s*min_machines_running\s*=\s*(\d+)\s*$", fly)
        self.assertIsNotNone(match)
        self.assertGreaterEqual(int(match.group(1)), 1)

        docker = (ROOT / "Dockerfile").read_text()
        self.assertIn("uv.lock", docker)
        self.assertIn("--frozen", docker)
        self.assertIn("--no-dev", docker)
        self.assertNotIn("--group dev", docker)
        self.assertNotIn("psycopg[binary]>=", docker)
        self.assertTrue("uv sync" in docker or "uv export" in docker)


if __name__ == "__main__":
    unittest.main()
