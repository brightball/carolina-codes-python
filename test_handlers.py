#!/usr/bin/env python3
"""Unit tests that drive shipped handlers and the shipped HTTP server."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import app

ROOT = Path(__file__).resolve().parent

SPEAKERS = {
    "ada": {
        "slug": "ada",
        "first_name": "Ada",
        "last_name": "Lovelace",
        "name": "Ada Lovelace",
    },
    "grace": {
        "slug": "grace",
        "first_name": "Grace",
        "last_name": "Hopper",
        "name": "Grace Hopper",
    },
    "linus": {
        "slug": "linus",
        "first_name": "Linus",
        "last_name": "Torvalds",
        "name": "Linus Torvalds",
    },
}

TALKS = [
    {
        "slug": "ada-2026",
        "title": "Analytical Engine",
        "description": "Analytical Engine",
        "format": "talk",
        "youtube_id": None,
        "year": 2026,
        "speaker_slug": "ada",
        "languages": ["python"],
        "topics": ["history"],
    },
    {
        "slug": "ada-2024",
        "title": "Notes",
        "description": "Notes",
        "format": "talk",
        "youtube_id": None,
        "year": 2024,
        "speaker_slug": "ada",
        "languages": ["python"],
        "topics": ["math"],
    },
    {
        "slug": "grace-2026",
        "title": "Compilers",
        "description": "Compilers",
        "format": "talk",
        "youtube_id": None,
        "year": 2026,
        "speaker_slug": "grace",
        "languages": ["cobol"],
        "topics": ["compilers"],
    },
    {
        "slug": "linus-2026",
        "title": "Kernels",
        "description": "Kernels",
        "format": "talk",
        "youtube_id": None,
        "year": 2026,
        "speaker_slug": "linus",
        "languages": ["c"],
        "topics": ["systems"],
    },
]

SPONSORS = {
    "acme": {
        "slug": "acme",
        "name": "Acme",
        "website": "https://acme.example",
        "logo_path": "/acme.png",
        "description": "Acme",
        "twitter_url": None,
        "linkedin_url": None,
        "youtube_url": None,
        "instagram_url": None,
        "facebook_url": None,
    },
    "globex": {
        "slug": "globex",
        "name": "Globex",
        "website": "https://globex.example",
        "logo_path": "/globex.png",
        "description": "Globex",
        "twitter_url": None,
        "linkedin_url": None,
        "youtube_url": None,
        "instagram_url": None,
        "facebook_url": None,
    },
}

YEAR_SPONSORS = [
    {**SPONSORS["acme"], "year": 2026, "tier": "gold", "featured": True, "blurb": "gold"},
    {**SPONSORS["acme"], "year": 2024, "tier": "silver", "featured": False, "blurb": "silver"},
    {**SPONSORS["globex"], "year": 2026, "tier": "bronze", "featured": False, "blurb": "bronze"},
]

SPONSORSHIPS = [
    {"sponsor_slug": "acme", "year": 2026, "tier": "gold"},
    {"sponsor_slug": "acme", "year": 2024, "tier": "silver"},
    {"sponsor_slug": "globex", "year": 2026, "tier": "bronze"},
]

YEARS = [
    {"year": 2026, "slug": "2026", "name": "2026", "status": "upcoming"},
    {"year": 2024, "slug": "2024", "name": "2024", "status": "past"},
]


def _copies(rows):
    return [dict(row) for row in rows]


def fake_query(sql, args):
    args = () if args is None else args
    if "FROM v1_years" in sql:
        return _copies(YEARS)
    # The year-scoped speaker query embeds `FROM v1_talks` in a subquery.
    if "FROM v1_speakers" in sql:
        if "slug = %s" in sql:
            row = SPEAKERS.get(args[0])
            return [dict(row)] if row else []
        if args:
            slugs = {talk["speaker_slug"] for talk in TALKS if talk["year"] == args[0]}
        else:
            slugs = set(SPEAKERS)
        rows = [dict(SPEAKERS[slug]) for slug in slugs]
        rows.sort(key=lambda row: (row["last_name"], row["first_name"]))
        return rows
    if "SELECT DISTINCT speaker_slug, year FROM v1_talks" in sql:
        wanted = set(args[0]) if args else set()
        rows = []
        for slug in sorted(wanted):
            years = sorted(
                {talk["year"] for talk in TALKS if talk["speaker_slug"] == slug},
                reverse=True,
            )
            for year in years:
                rows.append({"speaker_slug": slug, "year": year})
        return rows
    if "SELECT DISTINCT year FROM v1_talks" in sql:
        slug = args[0]
        years = sorted(
            {talk["year"] for talk in TALKS if talk["speaker_slug"] == slug}, reverse=True
        )
        return [{"year": year} for year in years]
    if "FROM v1_talks" in sql:
        if "speaker_slug = %s AND year = %s" in sql:
            slug, year = args
            found = [
                talk for talk in TALKS if talk["speaker_slug"] == slug and talk["year"] == year
            ]
        elif "speaker_slug = %s" in sql:
            slug = args[0]
            found = [talk for talk in TALKS if talk["speaker_slug"] == slug]
        elif "year = %s" in sql:
            year = args[0]
            found = [talk for talk in TALKS if talk["year"] == year]
        else:
            found = list(TALKS)
        return _copies(found)
    if "v1_year_sponsors" in sql:
        if "slug = %s" in sql:
            year, slug = args
            found = [row for row in YEAR_SPONSORS if row["year"] == year and row["slug"] == slug]
            return _copies(found[:1])
        return _copies([row for row in YEAR_SPONSORS if row["year"] == args[0]])
    if "v1_sponsorships" in sql:
        slug = args[0]
        if "DISTINCT year" in sql:
            years = sorted(
                {row["year"] for row in SPONSORSHIPS if row["sponsor_slug"] == slug},
                reverse=True,
            )
            return [{"year": year} for year in years]
        return _copies([row for row in SPONSORSHIPS if row["sponsor_slug"] == slug])
    if "v1_sponsors" in sql:
        if "slug = %s" in sql:
            row = SPONSORS.get(args[0])
            return [dict(row)] if row else []
        rows = [dict(row) for row in SPONSORS.values()]
        rows.sort(key=lambda row: row["name"])
        return rows
    return []


class FixtureCursor:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, args=None):
        self.rows = fake_query(sql, args)

    def fetchall(self):
        return self.rows


class FixtureConn:
    def __init__(self):
        self.closed = False

    def cursor(self):
        return FixtureCursor()

    def close(self):
        self.closed = True


def install_fake_catalog():
    app.reset_pool()
    app.reset_counts()
    app.CONNECT_FN = FixtureConn
    app.QUERY_FN = None


def uninstall_fake_catalog():
    app.CONNECT_FN = None
    app.QUERY_FN = None
    app.reset_pool()
    app.reset_counts()


def direct_fetch(path):
    parsed = urlparse(path)
    return app.handle_get(parsed.path, parse_qs(parsed.query))


def assert_routes(case, fetch):
    app.reset_counts()
    status, body = fetch("/")
    case.assertEqual(status, 200)
    case.assertEqual(body["language"], "Python")
    case.assertEqual(body["framework"], app.FRAMEWORK)
    case.assertEqual(body["api_version"], app.API_VERSION)
    case.assertIsInstance(body["endpoints"], list)
    paths = {item["path"] for item in body["endpoints"]}
    for required in (
        "/",
        "/health",
        "/v1/years",
        "/v1/speakers",
        "/v1/speakers/:slug",
        "/v1/speakers/:year/:slug",
        "/v1/sponsors",
        "/v1/sponsors/:slug",
        "/v1/sponsors/:year/:slug",
    ):
        case.assertIn(required, paths)
    case.assertEqual(app.SQL_COUNT, 0)
    case.assertEqual(app.CONNECT_COUNT, 0)

    status, body = fetch("/health")
    case.assertEqual(status, 200)
    case.assertEqual(body, {"ok": True})
    case.assertEqual(app.SQL_COUNT, 0)
    case.assertEqual(app.CONNECT_COUNT, 0)

    status, body = fetch("/v1/years")
    case.assertEqual(status, 200)
    case.assertEqual([row["year"] for row in body["data"]], [2026, 2024])
    case.assertEqual(app.CONNECT_COUNT, 1)

    status, body = fetch("/v1/speakers")
    case.assertEqual(status, 200)
    case.assertGreaterEqual(len(body["data"]), 3)
    case.assertEqual({row["slug"] for row in body["data"]}, {"ada", "grace", "linus"})

    app.SQL_COUNT = 0
    before = app.CONNECT_COUNT
    status, body = fetch("/v1/speakers?year=2026")
    case.assertEqual(status, 200)
    speakers = body["data"]
    case.assertGreaterEqual(len(speakers), 3)
    case.assertGreater(app.SQL_COUNT, 0)
    case.assertLessEqual(app.SQL_COUNT, 4)
    case.assertEqual(app.CONNECT_COUNT, before)
    for speaker in speakers:
        case.assertIn("talks", speaker)
        case.assertIn("languages", speaker)
        case.assertIn("topics", speaker)
        years = speaker["years"]
        case.assertEqual(years, sorted(years, reverse=True))
        case.assertTrue(speaker["talks"])
    ada = next(speaker for speaker in speakers if speaker["slug"] == "ada")
    case.assertEqual(ada["years"], [2026, 2024])
    case.assertIn("python", ada["languages"])

    status, body = fetch("/v1/speakers/ada")
    case.assertEqual(status, 200)
    case.assertGreaterEqual(len(body["data"]["talks"]), 2)
    case.assertEqual(body["data"]["years"], [2026, 2024])

    status, body = fetch("/v1/speakers/2026/ada")
    case.assertEqual(status, 200)
    data = body["data"]
    case.assertTrue(data["talks"])
    case.assertTrue(all(talk["year"] == 2026 for talk in data["talks"]))
    case.assertEqual(data["years"], [2026, 2024])
    case.assertEqual(data["other_years"], [2024])

    status, body = fetch("/v1/speakers/missing")
    case.assertEqual((status, body), (404, {"error": "not_found"}))

    status, body = fetch("/v1/sponsors")
    case.assertEqual(status, 200)
    case.assertIn("acme", {row["slug"] for row in body["data"]})

    status, body = fetch("/v1/sponsors?year=2026")
    case.assertEqual(status, 200)
    case.assertGreaterEqual(len(body["data"]), 1)
    case.assertTrue(all("slug" in row for row in body["data"]))

    status, body = fetch("/v1/sponsors/acme")
    case.assertEqual(status, 200)
    case.assertGreaterEqual(len(body["data"]["sponsorships"]), 1)

    status, body = fetch("/v1/sponsors/2026/acme")
    case.assertEqual(status, 200)
    case.assertEqual(body["data"]["years"], [2026, 2024])
    case.assertEqual(body["data"]["other_years"], [2024])

    status, body = fetch("/v1/sponsors/missing")
    case.assertEqual((status, body), (404, {"error": "not_found"}))
    case.assertEqual(app.CONNECT_COUNT, 1)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _read_url(url):
    try:
        with urllib.request.urlopen(url, timeout=2) as resp:
            status = resp.status
            headers = resp.headers
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        try:
            status = exc.code
            headers = exc.headers
            raw = exc.read()
        finally:
            exc.close()
    return {
        "status": status,
        "body": json.loads(raw.decode()),
        "content_type": headers.get("Content-Type", ""),
        "language": headers.get("X-Polyglot-Language"),
    }


class HandlerTests(unittest.TestCase):
    def setUp(self):
        install_fake_catalog()

    def tearDown(self):
        uninstall_fake_catalog()

    def test_route_matrix_and_pool_bounds(self):
        assert_routes(self, direct_fetch)

    def test_register_does_not_open_postgres(self):
        saved_url = os.environ.pop("CAROLINA_URL", None)
        saved_token = os.environ.pop("POLYGLOT_REGISTER_TOKEN", None)
        try:
            app.reset_counts()
            app.register("4004")
            self.assertEqual(app.SQL_COUNT, 0)
            self.assertEqual(app.CONNECT_COUNT, 0)
        finally:
            if saved_url is not None:
                os.environ["CAROLINA_URL"] = saved_url
            if saved_token is not None:
                os.environ["POLYGLOT_REGISTER_TOKEN"] = saved_token

    def test_failed_query_discards_connection(self):
        created = []

        class FlakyCursor:
            def __init__(self, conn):
                self.conn = conn
                self.rows = []

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def execute(self, sql, args=None):
                if self.conn.fail:
                    raise RuntimeError("query failed")
                self.rows = fake_query(sql, args)

            def fetchall(self):
                return self.rows

        class FlakyConn:
            def __init__(self, fail):
                self.fail = fail
                self.closed = False

            def cursor(self):
                return FlakyCursor(self)

            def close(self):
                self.closed = True

        def connect():
            conn = FlakyConn(fail=len(created) == 0)
            created.append(conn)
            return conn

        app.CONNECT_FN = connect
        app.QUERY_FN = None
        app.reset_pool()
        app.reset_counts()

        with self.assertRaises(RuntimeError):
            app.handle_get("/v1/years")
        self.assertEqual(len(created), 1)
        self.assertTrue(created[0].closed)
        self.assertNotIn(created[0], app._idle)
        self.assertEqual(app.CONNECT_COUNT, 1)

        status, payload = app.handle_get("/v1/years")
        self.assertEqual(status, 200)
        self.assertEqual([row["year"] for row in payload["data"]], [2026, 2024])
        self.assertEqual(app.CONNECT_COUNT, 2)
        self.assertEqual(len(created), 2)
        self.assertFalse(created[1].closed)
        self.assertNotIn(created[0], app._idle)
        self.assertIn(created[1], app._idle)


class ServerTests(unittest.TestCase):
    def setUp(self):
        install_fake_catalog()
        self.server = app.DualStackServer(("::", 0), app.Handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        uninstall_fake_catalog()

    def test_http_route_matrix_headers(self):
        def fetch(path):
            result = _read_url(f"http://127.0.0.1:{self.port}{path}")
            self.assertIn("application/json", result["content_type"])
            self.assertEqual(result["language"], "Python")
            return result["status"], result["body"]

        assert_routes(self, fetch)

    def test_http_recovers_after_failed_query(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        created = []

        class FlakyCursor:
            def __init__(self, conn):
                self.conn = conn
                self.rows = []

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def execute(self, sql, args=None):
                if self.conn.fail:
                    raise RuntimeError("query failed")
                self.rows = fake_query(sql, args)

            def fetchall(self):
                return self.rows

        class FlakyConn:
            def __init__(self, fail):
                self.fail = fail
                self.closed = False

            def cursor(self):
                return FlakyCursor(self)

            def close(self):
                self.closed = True

        def connect():
            conn = FlakyConn(fail=len(created) == 0)
            created.append(conn)
            return conn

        app.CONNECT_FN = connect
        app.QUERY_FN = None
        app.reset_pool()
        app.reset_counts()
        self.server = app.DualStackServer(("::", 0), app.Handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

        failed = _read_url(f"http://127.0.0.1:{self.port}/v1/years")
        self.assertEqual(failed["status"], 500)
        self.assertIn("application/json", failed["content_type"])
        self.assertEqual(failed["language"], "Python")
        self.assertTrue(created[0].closed)

        recovered = _read_url(f"http://127.0.0.1:{self.port}/v1/years")
        self.assertEqual(recovered["status"], 200)
        self.assertEqual([row["year"] for row in recovered["body"]["data"]], [2026, 2024])
        self.assertEqual(len(created), 2)
        self.assertEqual(app.CONNECT_COUNT, 2)

        health = _read_url(f"http://127.0.0.1:{self.port}/health")
        self.assertEqual(health["body"], {"ok": True})
        self.assertEqual(app.CONNECT_COUNT, 2)


class StartupTests(unittest.TestCase):
    def test_serve_answers_before_postgres(self):
        port = str(_free_port())
        saved = {
            key: os.environ.get(key) for key in ("PORT", "CAROLINA_URL", "POLYGLOT_REGISTER_TOKEN")
        }
        os.environ["PORT"] = port
        os.environ.pop("CAROLINA_URL", None)
        os.environ.pop("POLYGLOT_REGISTER_TOKEN", None)

        def boom():
            raise RuntimeError("unexpected connect")

        app.CONNECT_FN = boom
        app.QUERY_FN = None
        app.reset_pool()
        app.reset_counts()
        server = app.serve(port)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 5
            health = None
            while time.monotonic() < deadline:
                try:
                    health = _read_url(f"http://127.0.0.1:{port}/health")
                    break
                except (urllib.error.URLError, TimeoutError, ConnectionError):
                    time.sleep(0.02)
            self.assertIsNotNone(health)
            self.assertEqual(health["status"], 200)
            self.assertEqual(health["body"], {"ok": True})
            self.assertEqual(health["language"], "Python")
            self.assertIn("application/json", health["content_type"])
            identity = _read_url(f"http://127.0.0.1:{port}/")
            self.assertEqual(identity["status"], 200)
            self.assertEqual(identity["body"]["language"], "Python")
            self.assertTrue(identity["body"]["endpoints"])
            self.assertEqual(app.CONNECT_COUNT, 0)
            self.assertEqual(app.SQL_COUNT, 0)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
            app.CONNECT_FN = None
            app.QUERY_FN = None
            app.reset_pool()
            for key, value in saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    def test_process_answers_when_database_refuses(self):
        self._run_refused_launch()
        self._run_refused_launch()

    def _run_refused_launch(self):
        port = _free_port()
        db_port = _free_port()
        env = os.environ.copy()
        env["PORT"] = str(port)
        env["DATABASE_URL"] = f"postgres://postgres:postgres@127.0.0.1:{db_port}/carolina_dev"
        env["CAROLINA_URL"] = f"http://127.0.0.1:{db_port}"
        env["POLYGLOT_REGISTER_TOKEN"] = "dev"
        proc = subprocess.Popen(
            [sys.executable, str(ROOT / "app.py")],
            cwd=ROOT,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        health = identity = None
        error = None
        try:
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                if proc.poll() is not None:
                    error = "process exited early"
                    break
                try:
                    if health is None:
                        health = _read_url(f"http://127.0.0.1:{port}/health")
                    if identity is None:
                        identity = _read_url(f"http://127.0.0.1:{port}/")
                    if health is not None and identity is not None:
                        break
                except (urllib.error.URLError, TimeoutError, ConnectionError):
                    time.sleep(0.05)
            else:
                error = "timed out waiting for health and identity"
        finally:
            if proc.poll() is None:
                proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
            stderr = ""
            if proc.stderr is not None:
                stderr = proc.stderr.read()
                proc.stderr.close()
        self.assertIsNone(error, f"{error}\n{stderr}")
        self.assertEqual(health["status"], 200)
        self.assertEqual(health["body"], {"ok": True})
        self.assertIn("application/json", health["content_type"])
        self.assertEqual(health["language"], "Python")
        self.assertEqual(identity["status"], 200)
        self.assertEqual(identity["body"]["language"], "Python")
        self.assertTrue(identity["body"].get("endpoints"))
        self.assertIn("application/json", identity["content_type"])
        self.assertEqual(identity["language"], "Python")


if __name__ == "__main__":
    unittest.main()
