#!/usr/bin/env python3
"""Unit tests that drive shipped handlers without live Postgres."""

from __future__ import annotations

import unittest

import app


class FakeCursor:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, *args, **kwargs):
        raise AssertionError("QUERY_FN should intercept SQL")

    def fetchall(self):
        raise AssertionError("QUERY_FN should intercept SQL")


class FakeConn:
    def cursor(self):
        return FakeCursor()


def fake_query(sql, args):
    if "FROM v1_speakers" in sql:
        return [
            {"slug": f"s{i}", "first_name": "A", "last_name": "B", "name": f"A B {i}"}
            for i in range(3)
        ]
    if "ANY(" in sql or "speaker_slug, year" in sql:
        return [
            {"speaker_slug": "s0", "year": 2026},
            {"speaker_slug": "s0", "year": 2024},
            {"speaker_slug": "s1", "year": 2026},
            {"speaker_slug": "s2", "year": 2026},
        ]
    if "FROM v1_talks" in sql:
        return [
            {
                "slug": "t0",
                "title": "Talk",
                "speaker_slug": "s0",
                "year": 2026,
                "languages": ["python"],
                "topics": [],
            }
        ]
    if "FROM v1_years" in sql:
        return [{"year": 2026, "slug": "2026", "name": "2026", "status": "upcoming"}]
    return []


def install_fake_catalog():
    app.reset_pool()
    app.reset_counts()
    app.CONNECT_FN = FakeConn
    app.QUERY_FN = fake_query


def uninstall_fake_catalog():
    app.CONNECT_FN = None
    app.QUERY_FN = None
    app.reset_pool()
    app.reset_counts()


class HandlerTests(unittest.TestCase):
    def setUp(self):
        install_fake_catalog()

    def tearDown(self):
        uninstall_fake_catalog()

    def test_identity(self):
        status, body = app.handle_get("/")
        self.assertEqual(status, 200)
        self.assertEqual(body["language"], app.LANGUAGE)
        self.assertEqual(body["api_version"], app.API_VERSION)
        self.assertEqual(body["framework"], app.FRAMEWORK)
        self.assertEqual(body["schema_version"], app.SCHEMA_VERSION)
        paths = {ep["path"] for ep in body["endpoints"]}
        self.assertIn("/", paths)
        self.assertIn("/health", paths)
        self.assertIn("/v1/speakers", paths)
        self.assertEqual(app.SQL_COUNT, 0)
        self.assertEqual(app.CONNECT_COUNT, 0)

    def test_health(self):
        status, body = app.handle_get("/health")
        self.assertEqual(status, 200)
        self.assertEqual(body, {"ok": True})
        self.assertEqual(app.SQL_COUNT, 0)
        self.assertEqual(app.CONNECT_COUNT, 0)

    def test_speakers_catalog_listing(self):
        boot = app.CONNECT_COUNT
        status, payload = app.handle_get("/v1/speakers", {"year": ["2026"]})
        self.assertEqual(status, 200)
        speakers = payload["data"]
        self.assertGreaterEqual(len(speakers), 3)
        self.assertGreater(app.SQL_COUNT, 0)
        self.assertLessEqual(app.SQL_COUNT, 4)
        self.assertEqual(app.CONNECT_COUNT, boot + 1)
        multi = [sp for sp in speakers if len(sp.get("years") or []) >= 2]
        self.assertTrue(multi, "expected a speaker with >=2 years")
        for sp in multi:
            years = sp["years"]
            self.assertEqual(years, sorted(years, reverse=True))

        rows = app.list_speakers(None, 2026)
        self.assertGreaterEqual(len(rows), 3)
        self.assertEqual(rows[0]["slug"], "s0")


if __name__ == "__main__":
    unittest.main()
