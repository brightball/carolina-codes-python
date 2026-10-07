# carolina-codes-python

Read-only v1 polyglot HTTP API for the Carolina Code Conference. The Phoenix app (`Carolina.Polyglot`) keeps at most one language API warm and reads speakers and sponsors from it. With no API registered, the site falls back to Ash. This process serves ordinary JSON over the v1 REST and SQL-view contract.

This repo is its own git remote (`github.com/brightball/carolina-codes-python`). Do not assume a sibling CMS checkout (`../elixir` or any other path) is present. Do not fold this tree into the CMS remote (`github.com/brightball/carolina-codes`).

The HTTP contract is the CMS OpenAPI (`priv/api/openapi.yaml` and `priv/api/AGENTS.md` in the CMS repo), not a local starter `openapi.yaml`. Do not implement Ash JSON:API (`application/vnd.api+json`).

This file is the lean entrypoint. Durable choices are in [DECISIONS.md](DECISIONS.md). Repeated operational gotchas are in [MEMORY.md](MEMORY.md).

## When to update

Append a record to DECISIONS.md when a durable choice changes (framework, SQL surface, listen address, health behavior, lockfile or runtime split, quality-gate shape). Set the old record's status to superseded and leave the old text in place. Git history is the changelog.

Append a note to MEMORY.md when a repeated operational gotcha appears that the code does not make obvious. Keep decision records in DECISIONS.md.

Install, run, and test commands are in [README.md](README.md).

## Purpose

1. Query read-only `v1_*` views. Do not query Ash tables or base tables (`speakers`, `organizations`, `talks`, and the rest) as the public contract.
2. Expose the CMS v1 routes below.
3. Register once on boot and keep serving if `CAROLINA_URL` is unset or the POST fails. There is no heartbeat.

## Environment

| Variable | Example | Role |
|---|---|---|
| `DATABASE_URL` | `postgres://postgres:postgres@127.0.0.1:5432/carolina_dev` | CMS SQL views |
| `CAROLINA_URL` | `http://127.0.0.1:4000` | Elixir site (optional) |
| `POLYGLOT_REGISTER_TOKEN` | `dev` | Bearer token for register |
| `PUBLIC_BASE_URL` | `http://127.0.0.1:4004` | URL the Elixir site will call |
| `PORT` | `4004` locally, `8080` in the container | Listen port |

The `v1_*` views live in the CMS database. For live HTTP against the views, start Postgres 16 and set `DATABASE_URL` to the example above. This repo does not ship Compose, a Postgres image, or seed SQL. Handler tests that use a fake catalog need no Postgres.

## SQL

Catalog SQL uses psycopg against the views `v1_speakers`, `v1_sponsors`, `v1_years`, `v1_talks`, `v1_sponsorships`, and `v1_year_sponsors`. Statements are read-only. Year-scoped speaker rows include `languages` and `topics`. Year-scoped sponsor rows include `tier` and `blurb`.

`photo_path` and `logo_path` are web paths. Return the path. This process does not serve image bytes.

## HTTP

Wrap lists as `{ "data": [ ... ] }`. An unknown slug returns 404 `{"error": "not_found"}`.

- `GET /health` does not open Postgres and returns `{"ok": true}`. The starter template uses `{"status": "ok"}`; this service keeps `{"ok": true}`.
- `GET /` — identity (`language`, `language_version`, `api_version`, `framework`, `endpoints`)
- `GET /v1/years`
- `GET /v1/speakers` and `GET /v1/speakers?year=2025`
- `GET /v1/speakers/{slug}` and `GET /v1/speakers/{year}/{slug}`
- `GET /v1/sponsors` and `GET /v1/sponsors?year=2025`
- `GET /v1/sponsors/{slug}` and `GET /v1/sponsors/{year}/{slug}`

The framework is stdlib `http.server`. The process listens dual-stack IPv6 (`::` with `IPV6_V6ONLY` off) so Fly 6PN and IPv4 clients share one socket.

## Register on boot (once)

`POST {CAROLINA_URL}/internal/api-endpoints/register`

```
Authorization: Bearer {POLYGLOT_REGISTER_TOKEN}
Content-Type: application/json
```

Body fields: `language`, `language_version`, `api_version`, `framework`, `created_year`, `base_url` (`PUBLIC_BASE_URL`), `schema_version` (1), `endpoints`.

Registration runs once, on a daemon thread, and must not open psycopg. If `CAROLINA_URL` is empty or the POST fails (connection refused, 4xx, or 5xx), log and keep serving.

## Quality gates

Quality gates are tests, Ruff, Bandit, locked pip-audit, and gitleaks, via `pre-commit run --all-files` after `uv sync --group dev`. Gitea (`.gitea/workflows/precommit.yml`) prepares the environment once, then runs parallel Gitea jobs (one job per check).

## Layout

| Path | Role |
|---|---|
| `app.py` | stdlib `http.server` API |
| `pyproject.toml`, `uv.lock` | Python `>=3.11`, psycopg 3, uv lock |
| `Dockerfile` | `python:3.12` image, uv `0.11.21`, dev tools omitted |
| `test_handlers.py`, `perf_test.py` | Fake-catalog tests |
| `DECISIONS.md` | Durable choices (context, decision, consequences) |
| `MEMORY.md` | Operational gotchas |
