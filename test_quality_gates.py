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

    def test_fly_scales_to_zero_and_image_uses_the_lockfile(self):
        fly = (ROOT / "fly.toml").read_text()
        self.assertIn('auto_stop_machines = "stop"', fly)
        self.assertIn("auto_start_machines = true", fly)
        self.assertIn("internal_port = 8080", fly)
        self.assertIn('method = "GET"', fly)
        self.assertIn('path = "/health"', fly)
        match = re.search(r"(?m)^\s*min_machines_running\s*=\s*(\d+)\s*$", fly)
        self.assertIsNotNone(match)
        self.assertEqual(int(match.group(1)), 0)

        docker = (ROOT / "Dockerfile").read_text()
        self.assertIn("uv.lock", docker)
        self.assertIn("--frozen", docker)
        self.assertIn("--no-dev", docker)
        self.assertNotIn("--group dev", docker)
        self.assertNotIn("psycopg[binary]>=", docker)
        self.assertTrue("uv sync" in docker or "uv export" in docker)


DOC_NAMES = ("AGENTS.md", "README.md", "MEMORY.md", "DECISIONS.md")

_PG_URL = re.compile(r"postgres(?:ql)?://[^\s)\]>'\"`]+", re.I)
_ALLOWED_DSN = "postgres://postgres:postgres@127.0.0.1:5432/carolina_dev"
_TOKEN_PREFIX = re.compile(
    r"(?:ghp_|gho_|ghu_|ghs_|ghr_|github_pat_|glpat-|xox[abprs]-|sk-|AKIA)[A-Za-z0-9_-]{4,}"
)
_TAILNET_HOST = re.compile(r"\b[\w.-]+\.ts\.net\b", re.I)

AGENTS_PHRASES = (
    "read-only `v1_*` views",
    "Do not query Ash tables",
    "Register once on boot",
    "keep serving if `CAROLINA_URL` is unset or the POST fails",
    "`DATABASE_URL`",
    "`CAROLINA_URL`",
    "`POLYGLOT_REGISTER_TOKEN`",
    "`PUBLIC_BASE_URL`",
    "`PORT`",
    "its own git remote",
    "Do not assume a sibling CMS checkout",
    "CMS OpenAPI",
    "not a local starter `openapi.yaml`",
    "stdlib `http.server`",
    "GET /health` does not open Postgres",
    '{"ok": true}',
    '{"status": "ok"}',
    "dual-stack IPv6",
    "Catalog SQL uses psycopg",
    "Ruff",
    "Bandit",
    "pip-audit",
    "gitleaks",
    "pre-commit",
    "one job per check",
    "Append a record to DECISIONS.md when a durable choice changes",
    "Append a note to MEMORY.md when a repeated operational gotcha appears",
    "](MEMORY.md)",
    "](DECISIONS.md)",
)

DECISION_PHRASES = (
    "stdlib `http.server` instead of a third-party web framework",
    "psycopg 3",
    "uv lockfile",
    "dev tools excluded from the runtime image",
    "health checks before any Postgres connection",
    "dual-stack IPv6",
    "Fly 6PN",
    "Views-only SQL",
    "pre-commit",
    "one Gitea job per check",
    "**Status:**",
    "**Context:**",
    "**Decision:**",
    "**Consequences:**",
)

MEMORY_PHRASES = (
    "fake catalog",
    "need no Postgres",
    "4004",
    "8080",
    "sslmode=disable",
    "appended when",
    "must not open psycopg",
    "gitleaks",
    _ALLOWED_DSN,
    "DECISIONS.md",
)

README_PHRASES = (
    ">=3.11",
    "python:3.12",
    "http.server",
    "no separate framework package",
    "framework version is that Python version",
    "psycopg[binary]>=3.2",
    "0.11.21",
    "uv",
    "Ruff",
    "Bandit",
    "pip-audit",
    "pre-commit",
    "gitleaks",
)


def _private_hits(text: str) -> list[str]:
    hits: list[str] = []
    if "x-access-token" in text.lower():
        hits.append("x-access-token")
    hits.extend(match.group(0) for match in _TOKEN_PREFIX.finditer(text))
    hits.extend(match.group(0) for match in _TAILNET_HOST.finditer(text))
    for match in _PG_URL.finditer(text):
        url = match.group(0).rstrip(".,")
        if url != _ALLOWED_DSN:
            hits.append(url)
    return hits


class DocContractTests(unittest.TestCase):
    def _read(self, name: str) -> str:
        path = ROOT / name
        self.assertTrue(path.is_file(), f"missing {name}")
        return path.read_text()

    def test_agents_records_contract_divergences_and_doc_links(self):
        text = self._read("AGENTS.md")
        for phrase in AGENTS_PHRASES:
            self.assertIn(phrase, text, phrase)

    def test_decisions_record_context_and_consequences(self):
        text = self._read("DECISIONS.md")
        for phrase in DECISION_PHRASES:
            self.assertIn(phrase, text, phrase)
        self.assertGreaterEqual(text.count("**Context:**"), 6)
        self.assertGreaterEqual(text.count("**Consequences:**"), 6)
        self.assertGreaterEqual(text.count("**Decision:**"), 6)

    def test_memory_indexes_gotchas_without_copying_decisions(self):
        text = self._read("MEMORY.md")
        for phrase in MEMORY_PHRASES:
            self.assertIn(phrase, text, phrase)
        self.assertNotIn("third-party web framework", text)
        self.assertNotIn("one Gitea job per check", text)
        self.assertNotIn("**Consequences:**", text)

    def test_readme_states_language_framework_and_notable_packages(self):
        text = self._read("README.md")
        for phrase in README_PHRASES:
            self.assertIn(phrase, text, phrase)
        self.assertIn("uv sync --group dev", text)
        self.assertIn("pre-commit run --all-files", text)
        self.assertIn("bandit -c pyproject.toml -r app.py scripts", text)

    def test_docs_have_no_private_data(self):
        for name in DOC_NAMES:
            text = self._read(name)
            hits = _private_hits(text)
            self.assertEqual(hits, [], f"{name} private-data hits: {hits}")


if __name__ == "__main__":
    unittest.main()
