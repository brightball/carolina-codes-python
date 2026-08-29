#!/usr/bin/env python3
"""Carolina Code Conference polyglot API — Python."""

from __future__ import annotations

import json
import os
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

DSN = os.environ.get("DATABASE_URL", "postgres://postgres:postgres@127.0.0.1:5432/carolina_dev")


def db():
    return psycopg.connect(DSN, row_factory=dict_row)


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
        cur.execute(
            f"SELECT {TALK_COLS} FROM v1_talks WHERE speaker_slug = %s ORDER BY year DESC",
            (slug,),
        )
    else:
        cur.execute(
            f"SELECT {TALK_COLS} FROM v1_talks WHERE speaker_slug = %s AND year = %s ORDER BY year DESC",
            (slug, year),
        )
    return [clean(r) for r in cur.fetchall()]


def uniq_tags(talks, key):
    seen, out = set(), []
    for talk in talks:
        for val in talk.get(key) or []:
            if val and val not in seen:
                seen.add(val)
                out.append(val)
    return out


def talk_years(cur, slug):
    cur.execute(
        "SELECT DISTINCT year FROM v1_talks WHERE speaker_slug = %s ORDER BY year DESC",
        (slug,),
    )
    return [r["year"] for r in cur.fetchall()]


def sponsor_years(cur, slug):
    cur.execute(
        "SELECT DISTINCT year FROM v1_sponsorships WHERE sponsor_slug = %s ORDER BY year DESC",
        (slug,),
    )
    return [r["year"] for r in cur.fetchall()]


def load_speaker(cur, slug):
    cur.execute(f"SELECT {SPEAKER_COLS} FROM v1_speakers WHERE slug = %s", (slug,))
    return clean(cur.fetchone())


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
        parts = [p for p in path.split("/") if p]
        try:
            with db() as conn, conn.cursor() as cur:
                self.route(cur, path, parts, qs)
        except Exception as exc:
            self.send_json({"error": str(exc)}, 500)

    def route(self, cur, path, parts, qs):
        if path == "/":
            return self.send_json(
                {
                    "language": LANGUAGE,
                    "language_version": LANGUAGE_VERSION,
                    "api_version": API_VERSION,
                    "framework": FRAMEWORK,
                    "created_year": CREATED_YEAR,
                    "schema_version": SCHEMA_VERSION,
                    "endpoints": ENDPOINTS,
                }
            )
        if path == "/health":
            return self.send_json({"ok": True})
        if path == "/v1/years":
            cur.execute("SELECT year, slug, name, status FROM v1_years ORDER BY year DESC")
            return self.send_json({"data": [clean(r) for r in cur.fetchall()]})
        if path == "/v1/speakers":
            year = qs.get("year", [None])[0]
            if year:
                y = int(year)
                cur.execute(
                    f"SELECT {SPEAKER_COLS} FROM v1_speakers "
                    "WHERE slug IN (SELECT speaker_slug FROM v1_talks WHERE year = %s) "
                    "ORDER BY last_name, first_name",
                    (y,),
                )
                speakers = []
                for row in cur.fetchall():
                    sp = clean(row)
                    talks = talks_for(cur, sp["slug"], y)
                    years = talk_years(cur, sp["slug"])
                    sp.update(
                        {
                            "year": y,
                            "talks": talks,
                            "languages": uniq_tags(talks, "languages"),
                            "topics": uniq_tags(talks, "topics"),
                            "years": years,
                        }
                    )
                    speakers.append(sp)
                return self.send_json({"data": speakers})
            cur.execute(f"SELECT {SPEAKER_COLS} FROM v1_speakers ORDER BY last_name, first_name")
            return self.send_json({"data": [clean(r) for r in cur.fetchall()]})
        if len(parts) == 4 and parts[0] == "v1" and parts[1] == "speakers" and parts[2].isdigit():
            y, slug = int(parts[2]), parts[3]
            speaker = load_speaker(cur, slug)
            if not speaker:
                return self.send_json({"error": "not_found"}, 404)
            talks = talks_for(cur, slug, y)
            if not talks:
                return self.send_json({"error": "not_found"}, 404)
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
            return self.send_json({"data": speaker})
        if len(parts) == 3 and parts[0] == "v1" and parts[1] == "speakers":
            slug = parts[2]
            speaker = load_speaker(cur, slug)
            if not speaker:
                return self.send_json({"error": "not_found"}, 404)
            speaker["talks"] = talks_for(cur, slug)
            speaker["years"] = talk_years(cur, slug)
            return self.send_json({"data": speaker})
        if path == "/v1/sponsors":
            year = qs.get("year", [None])[0]
            if year:
                cur.execute(
                    f"SELECT {YEAR_SPONSOR_COLS} FROM v1_year_sponsors WHERE year = %s ORDER BY name",
                    (int(year),),
                )
            else:
                cur.execute(f"SELECT {SPONSOR_COLS} FROM v1_sponsors ORDER BY name")
            return self.send_json({"data": [clean(r) for r in cur.fetchall()]})
        if len(parts) == 4 and parts[0] == "v1" and parts[1] == "sponsors" and parts[2].isdigit():
            y, slug = int(parts[2]), parts[3]
            cur.execute(
                f"SELECT {YEAR_SPONSOR_COLS} FROM v1_year_sponsors WHERE year = %s AND slug = %s",
                (y, slug),
            )
            row = clean(cur.fetchone())
            if not row:
                return self.send_json({"error": "not_found"}, 404)
            years = sponsor_years(cur, slug)
            row["years"] = years
            row["other_years"] = [n for n in years if n != y]
            return self.send_json({"data": row})
        if len(parts) == 3 and parts[0] == "v1" and parts[1] == "sponsors":
            slug = parts[2]
            cur.execute(f"SELECT {SPONSOR_COLS} FROM v1_sponsors WHERE slug = %s", (slug,))
            row = clean(cur.fetchone())
            if not row:
                return self.send_json({"error": "not_found"}, 404)
            cur.execute("SELECT * FROM v1_sponsorships WHERE sponsor_slug = %s", (slug,))
            row["sponsorships"] = [clean(r) for r in cur.fetchall()]
            return self.send_json({"data": row})
        self.send_json({"error": "not_found"}, 404)


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
    port = os.environ.get("PORT", "4004")
    threading.Thread(target=register, args=(port,), daemon=True).start()
    server = ThreadingHTTPServer(("0.0.0.0", int(port)), Handler)
    print(f"carolina-codes-python listening on :{port}", file=sys.stderr)
    server.serve_forever()


if __name__ == "__main__":
    main()
