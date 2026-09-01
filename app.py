#!/usr/bin/env python3
"""Carolina Code Conference polyglot API — Python."""

from __future__ import annotations

import json
import os
import socket
import sys
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import psycopg
from psycopg.rows import dict_row

LANGUAGE = "Python"
API_VERSION = "0.2.0"
FRAMEWORK = "http.server"
CREATED_YEAR = 2026
SCHEMA_VERSION = 1
LANGUAGE_VERSION = sys.version.split()[0]

ENDPOINTS = [
    {"method": "GET", "path": "/", "query": []},
    {"method": "GET", "path": "/health", "query": []},
    {"method": "GET", "path": "/v1/years", "query": []},
    {"method": "GET", "path": "/v1/speakers", "query": ["year"]},
    {"method": "GET", "path": "/v1/speakers/:slug", "query": []},
    {"method": "GET", "path": "/v1/speakers/:year/:slug", "query": []},
    {"method": "GET", "path": "/v1/sponsors", "query": ["year"]},
    {"method": "GET", "path": "/v1/sponsors/:slug", "query": []},
    {"method": "GET", "path": "/v1/sponsors/:year/:slug", "query": []},
]

SPEAKER_COLS = (
    "slug, first_name, last_name, name, tagline, bio, company, location, "
    "photo_path, twitter_url, linkedin_url, website_url, github_url, featured"
)
YEAR_SPONSOR_COLS = (
    "slug, name, website, logo_path, description, blurb, tier, featured, year, "
    "twitter_url, linkedin_url, youtube_url, instagram_url, facebook_url"
)
SPONSOR_COLS = (
    "slug, name, website, logo_path, description, twitter_url, linkedin_url, "
    "youtube_url, instagram_url, facebook_url"
)
TALK_COLS = "slug, title, description, format, youtube_id, year, speaker_slug, languages, topics"

POOL_SIZE = 8
SQL_COUNT = 0
CONNECT_COUNT = 0
CONNECT_FN = None
QUERY_FN = None

_pool_lock = threading.Condition()
_idle: list = []
_opened = 0
_ready = False
_count_lock = threading.Lock()


def listen_host() -> str:
    return "::"


def reset_counts() -> None:
    global SQL_COUNT, CONNECT_COUNT
    with _count_lock:
        SQL_COUNT = 0
        CONNECT_COUNT = 0


def dsn() -> str:
    raw = os.environ.get("DATABASE_URL", "postgres://postgres:postgres@127.0.0.1:5432/carolina_dev")
    if "sslmode=" not in raw:
        raw += ("&" if "?" in raw else "?") + "sslmode=disable"
    return raw


def open_connection():
    global CONNECT_COUNT
    with _count_lock:
        CONNECT_COUNT += 1
    if CONNECT_FN:
        return CONNECT_FN()
    return psycopg.connect(dsn(), row_factory=dict_row, autocommit=True)


def open_pool() -> None:
    global _opened, _ready
    with _pool_lock:
        if _ready:
            return
        _idle.append(open_connection())
        _opened = 1
        _ready = True


def acquire():
    global _opened, _ready
    with _pool_lock:
        if not _ready:
            _idle.append(open_connection())
            _opened = 1
            _ready = True
        while True:
            if _idle:
                return _idle.pop()
            if _opened < POOL_SIZE:
                _opened += 1
                return open_connection()
            _pool_lock.wait()


def release(conn) -> None:
    if conn is None:
        return
    with _pool_lock:
        _idle.append(conn)
        _pool_lock.notify()


def db_query(cur, sql: str, args=None):
    global SQL_COUNT
    with _count_lock:
        SQL_COUNT += 1
    if QUERY_FN:
        return QUERY_FN(sql, args)
    if args is None:
        cur.execute(sql)
    else:
        cur.execute(sql, args)
    return list(cur.fetchall())


def db_query_one(cur, sql: str, args=None):
    rows = db_query(cur, sql, args)
    return rows[0] if rows else None


def clean(row):
    if row is None:
        return None
    out = {}
    for k, v in row.items():
        if hasattr(v, "isoformat"):
            out[k] = str(v)
        elif isinstance(v, list):
            out[k] = [str(x) for x in v]
        else:
            out[k] = v
    return out


def talks_for(cur, slug, year=None):
    if year is None:
        rows = db_query(
            cur,
            f"SELECT {TALK_COLS} FROM v1_talks WHERE speaker_slug = %s ORDER BY year DESC",
            (slug,),
        )
    else:
        rows = db_query(
            cur,
            f"SELECT {TALK_COLS} FROM v1_talks WHERE speaker_slug = %s AND year = %s ORDER BY year DESC",
            (slug, year),
        )
    return [clean(r) for r in rows]


def uniq_tags(talks, key):
    seen, out = set(), []
    for talk in talks:
        for val in talk.get(key) or []:
            if val and val not in seen:
                seen.add(val)
                out.append(val)
    return out


def talk_years(cur, slug):
    rows = db_query(
        cur,
        "SELECT DISTINCT year FROM v1_talks WHERE speaker_slug = %s ORDER BY year DESC",
        (slug,),
    )
    return [r["year"] for r in rows]


def sponsor_years(cur, slug):
    rows = db_query(
        cur,
        "SELECT DISTINCT year FROM v1_sponsorships WHERE sponsor_slug = %s ORDER BY year DESC",
        (slug,),
    )
    return [r["year"] for r in rows]


def load_speaker(cur, slug):
    return clean(db_query_one(cur, f"SELECT {SPEAKER_COLS} FROM v1_speakers WHERE slug = %s", (slug,)))


def list_speakers(cur, year=None):
    if year is None:
        rows = db_query(cur, f"SELECT {SPEAKER_COLS} FROM v1_speakers ORDER BY last_name, first_name")
        return [clean(r) for r in rows]
    rows = db_query(
        cur,
        f"SELECT {SPEAKER_COLS} FROM v1_speakers "
        "WHERE slug IN (SELECT speaker_slug FROM v1_talks WHERE year = %s) "
        "ORDER BY last_name, first_name",
        (year,),
    )
    return attach_year_tags(cur, [clean(r) for r in rows], year)


def attach_year_tags(cur, speakers, year):
    if not speakers:
        return speakers
    slugs = [sp["slug"] for sp in speakers]
    talks_by = load_talks_for_year(cur, year)
    years_by = load_years_for_slugs(cur, slugs)
    for sp in speakers:
        slug = sp["slug"]
        talks = talks_by.get(slug, [])
        years = years_by.get(slug, [])
        sp.update(
            {
                "year": year,
                "talks": talks,
                "languages": uniq_tags(talks, "languages"),
                "topics": uniq_tags(talks, "topics"),
                "years": years,
            }
        )
    return speakers


def load_talks_for_year(cur, year):
    rows = db_query(
        cur,
        f"SELECT {TALK_COLS} FROM v1_talks WHERE year = %s ORDER BY speaker_slug, year DESC",
        (year,),
    )
    out = {}
    for row in rows:
        talk = clean(row)
        slug = talk.get("speaker_slug") or ""
        out.setdefault(slug, []).append(talk)
    return out


def load_years_for_slugs(cur, slugs):
    if not slugs:
        return {}
    rows = db_query(
        cur,
        "SELECT DISTINCT speaker_slug, year FROM v1_talks "
        "WHERE speaker_slug = ANY(%s) ORDER BY speaker_slug, year DESC",
        (list(slugs),),
    )
    out = {}
    for row in rows:
        out.setdefault(row["speaker_slug"], []).append(row["year"])
    return out


def dispatch(cur, path, parts, qs):
    if path == "/":
        return 200, {
            "language": LANGUAGE,
            "language_version": LANGUAGE_VERSION,
            "api_version": API_VERSION,
            "framework": FRAMEWORK,
            "created_year": CREATED_YEAR,
            "schema_version": SCHEMA_VERSION,
            "endpoints": ENDPOINTS,
        }
    if path == "/health":
        return 200, {"ok": True}
    if path == "/v1/years":
        rows = db_query(cur, "SELECT year, slug, name, status FROM v1_years ORDER BY year DESC")
        return 200, {"data": [clean(r) for r in rows]}
    if path == "/v1/speakers":
        year = qs.get("year", [None])[0]
        y = int(year) if year else None
        return 200, {"data": list_speakers(cur, y)}
    if len(parts) == 4 and parts[0] == "v1" and parts[1] == "speakers" and parts[2].isdigit():
        y, slug = int(parts[2]), parts[3]
        speaker = load_speaker(cur, slug)
        if not speaker:
            return 404, {"error": "not_found"}
        talks = talks_for(cur, slug, y)
        if not talks:
            return 404, {"error": "not_found"}
        years = talk_years(cur, slug)
        speaker.update(
            {
                "year": y,
                "years": years,
                "other_years": [n for n in years if n != y],
                "talks": talks,
                "languages": uniq_tags(talks, "languages"),
                "topics": uniq_tags(talks, "topics"),
            }
        )
        return 200, {"data": speaker}
    if len(parts) == 3 and parts[0] == "v1" and parts[1] == "speakers":
        slug = parts[2]
        speaker = load_speaker(cur, slug)
        if not speaker:
            return 404, {"error": "not_found"}
        speaker["talks"] = talks_for(cur, slug)
        speaker["years"] = talk_years(cur, slug)
        return 200, {"data": speaker}
    if path == "/v1/sponsors":
        year = qs.get("year", [None])[0]
        if year:
            rows = db_query(
                cur,
                f"SELECT {YEAR_SPONSOR_COLS} FROM v1_year_sponsors WHERE year = %s ORDER BY name",
                (int(year),),
            )
        else:
            rows = db_query(cur, f"SELECT {SPONSOR_COLS} FROM v1_sponsors ORDER BY name")
        return 200, {"data": [clean(r) for r in rows]}
    if len(parts) == 4 and parts[0] == "v1" and parts[1] == "sponsors" and parts[2].isdigit():
        y, slug = int(parts[2]), parts[3]
        row = clean(
            db_query_one(
                cur,
                f"SELECT {YEAR_SPONSOR_COLS} FROM v1_year_sponsors WHERE year = %s AND slug = %s",
                (y, slug),
            )
        )
        if not row:
            return 404, {"error": "not_found"}
        years = sponsor_years(cur, slug)
        row["years"] = years
        row["other_years"] = [n for n in years if n != y]
        return 200, {"data": row}
    if len(parts) == 3 and parts[0] == "v1" and parts[1] == "sponsors":
        slug = parts[2]
        row = clean(db_query_one(cur, f"SELECT {SPONSOR_COLS} FROM v1_sponsors WHERE slug = %s", (slug,)))
        if not row:
            return 404, {"error": "not_found"}
        rows = db_query(cur, "SELECT * FROM v1_sponsorships WHERE sponsor_slug = %s", (slug,))
        row["sponsorships"] = [clean(r) for r in rows]
        return 200, {"data": row}
    return 404, {"error": "not_found"}


def handle_get(path, qs=None):
    qs = qs or {}
    parts = [p for p in path.split("/") if p]
    if path in ("/", "/health"):
        return dispatch(None, path, parts, qs)
    conn = acquire()
    try:
        with conn.cursor() as cur:
            return dispatch(cur, path, parts, qs)
    finally:
        release(conn)


class DualStackServer(ThreadingHTTPServer):
    address_family = socket.AF_INET6

    def server_bind(self):
        self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        super().server_bind()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def send_json(self, payload, status=200):
        body = json.dumps(payload, default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("X-Polyglot-Language", LANGUAGE)
        self.send_header("X-Polyglot-Framework", FRAMEWORK)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        qs = parse_qs(parsed.query)
        try:
            status, payload = handle_get(path, qs)
            self.send_json(payload, status)
        except Exception as exc:
            self.send_json({"error": str(exc)}, 500)


def register(port: str) -> None:
    url = os.environ.get("CAROLINA_URL")
    token = os.environ.get("POLYGLOT_REGISTER_TOKEN")
    if not url or not token:
        return
    base = os.environ.get("PUBLIC_BASE_URL", f"http://127.0.0.1:{port}")
    body = json.dumps(
        {
            "language": LANGUAGE,
            "language_version": LANGUAGE_VERSION,
            "api_version": API_VERSION,
            "framework": FRAMEWORK,
            "created_year": CREATED_YEAR,
            "schema_version": SCHEMA_VERSION,
            "base_url": base,
            "endpoints": ENDPOINTS,
        }
    ).encode()
    req = urllib.request.Request(
        url.rstrip("/") + "/internal/api-endpoints/register",
        data=body,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            print(f"registered with elixir: {resp.status}", file=sys.stderr)
    except Exception as exc:
        print(f"register: {exc}", file=sys.stderr)


def main():
    open_pool()
    port = os.environ.get("PORT", "4004")
    threading.Thread(target=register, args=(port,), daemon=True).start()
    server = DualStackServer((listen_host(), int(port)), Handler)
    print(f"carolina-codes-python listening on :{port}", file=sys.stderr)
    server.serve_forever()


if __name__ == "__main__":
    main()
