#!/usr/bin/env python3
"""Performance tests for the shipped Python polyglot API."""

from __future__ import annotations

import socket
import sys
from pathlib import Path

import app
from test_handlers import install_fake_catalog

FAILED = 0


def expect(cond, msg):
    global FAILED
    if cond:
        print(f"ok: {msg}", file=sys.stderr)
    else:
        print(f"FAIL: {msg}", file=sys.stderr)
        FAILED += 1


def assert_years_desc(speakers, label):
    found_multi = False
    for sp in speakers:
        years = sp.get("years") or []
        if len(years) < 2:
            continue
        found_multi = True
        for a, b in zip(years, years[1:]):
            if a < b:
                expect(False, f"{label} years not DESC for {sp.get('slug')}: {years}")
                return
    expect(found_multi, f"{label} expected a speaker with >=2 years")


src = Path(__file__).with_name("app.py").read_text()

expect(app.listen_host() == "::", "listen host is ::")
expect('("0.0.0.0"' not in src, "source does not bind 0.0.0.0")
expect("listen_host()" in src, "server uses listen_host()")
expect("sslmode=disable" in src, "DSN keeps sslmode=disable")
expect("IPV6_V6ONLY" in src, "IPv6 bind sets IPV6_V6ONLY")
expect(app.DualStackServer.address_family == socket.AF_INET6, "DualStackServer is AF_INET6")
expect("sslmode=disable" in app.dsn(), "dsn() includes sslmode=disable")

reg = src.find("def register(")
expect(reg >= 0, "register exists")
if reg >= 0:
    fn = src[reg:]
    end = fn.find("\ndef main(")
    if end > 0:
        fn = fn[:end]
    expect("open_pool()" not in fn, "register-once does not open the pool")
    expect("open_connection()" not in fn, "register-once does not open Postgres")
    expect("db_query(" not in fn, "register-once does not run catalog SQL")
    expect("psycopg.connect" not in fn, "register-once does not open psycopg")

app.reset_counts()
istatus, ibody = app.handle_get("/")
expect(istatus == 200, "/ identity returns 200")
expect(ibody.get("language") == app.LANGUAGE, "/ identity language is Python")
expect(ibody.get("api_version") == app.API_VERSION, "/ identity includes api_version")
expect(app.SQL_COUNT == 0, "/ identity does not run SQL")
expect(app.CONNECT_COUNT == 0, "/ identity does not open Postgres")

app.reset_counts()
hstatus, hbody = app.handle_get("/health")
expect(hstatus == 200, "/health returns 200")
expect(hbody.get("ok") is True, "/health body is ok JSON")
expect(app.SQL_COUNT == 0, "/health does not run SQL")
expect(app.CONNECT_COUNT == 0, "/health does not open Postgres")

live = False
try:
    app.open_pool()
    live = True
except Exception as exc:
    print(f"postgres unavailable, using query hook: {exc}", file=sys.stderr)
    install_fake_catalog()
    app.open_pool()

boot = app.CONNECT_COUNT
app.SQL_COUNT = 0

status, payload = app.handle_get("/v1/speakers", {"year": ["2026"]})
speakers = payload.get("data") if isinstance(payload, dict) else []
n = len(speakers) if isinstance(speakers, list) else 0
sql = app.SQL_COUNT
print(
    f"year list status={status} sql={sql} speakers={n} connects={app.CONNECT_COUNT}",
    file=sys.stderr,
)

if live and status != 200:
    expect(False, f"live year listing status {status} body {payload}")

if status == 200:
    expect(n >= 3, "year listing returns N>=3 speakers")
    expect(sql > 0, "listing runs SQL through shipped query wrapper")
    expect(sql < 2 * n, "SQL count does not grow as ~2N")
    expect(sql <= 4, "year listing SQL is bounded (speakers + talks + years)")
    assert_years_desc(speakers, "handler")
    expect(app.CONNECT_COUNT == boot, "listing reuses the boot pool")

    rows = app.list_speakers(None, 2026) if app.QUERY_FN else None
    if rows is None:
        conn = app.acquire()
        try:
            with conn.cursor() as cur:
                rows = app.list_speakers(cur, 2026)
        finally:
            app.release(conn)
    assert_years_desc(rows, "list_speakers")

    app.SQL_COUNT = 0
    status2, _ = app.handle_get("/v1/speakers", {"year": ["2026"]})
    expect(status2 == 200, "second catalog request succeeds")
    expect(app.CONNECT_COUNT == boot, "second catalog request reuses pool (no extra connect)")
else:
    expect(sql < 2 * 3, "failed listing did not run per-row SQL for N=3")

if FAILED:
    print("perf_test failed", file=sys.stderr)
    sys.exit(1)
print("perf_test passed", file=sys.stderr)
