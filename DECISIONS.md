# Decisions

Durable choices for this service (Python, stdlib `http.server`, psycopg 3). There is no framework ADR tool for this stack, so records live in this file.

Use the fields below on every record: status, context, decision, consequences. That is the lightweight form of a Nygard architecture decision record. Git history is the changelog. When a choice changes, append a new record and set the old record's status to `superseded`. Leave the old text in place.

Operational gotchas that are not decisions belong in [MEMORY.md](MEMORY.md). The entrypoint for agents is [AGENTS.md](AGENTS.md).

## stdlib `http.server` instead of a third-party web framework

- **Status:** accepted (2026-08-28)
- **Context:** The polyglot starter is a replaceable language runtime. This API routes a fixed set of GET paths, writes JSON, and handles one request per thread. A third-party web framework would be an extra dependency the image does not need.
- **Decision:** Implement the server with `http.server.BaseHTTPRequestHandler` and `ThreadingHTTPServer`. The reported framework name is `http.server`. It has no package of its own; the framework version is the running Python version.
- **Consequences:** `pyproject.toml` has no web-framework dependency. Keepalives, the listen socket, and JSON headers stay in `app.py`. Adopting another server is a new record.

## psycopg 3 and a uv lockfile, with dev tools excluded from the runtime image

- **Status:** accepted (2026-08-28)
- **Context:** Catalog reads need a maintained PostgreSQL driver. The runtime image should contain that driver and the application, and should omit linters, audit tools, and the test runner.
- **Decision:** Depend on `psycopg[binary]>=3.2`. Lock dependencies with uv in `uv.lock`. The image pins uv `0.11.21` and installs with `uv sync --frozen --no-dev`.
- **Consequences:** Ruff, Bandit, pip-audit, and pre-commit stay in the dev group. Change the locked set with uv. Do not `pip install` the runtime dependencies inside the image.

## Accept health checks before any Postgres connection

- **Status:** accepted (2026-09-01, kept on 2026-09-22)
- **Context:** Fly probes `GET /health`. A database outage or a slow pool must not fail liveness. `GET /` is also identity data that does not need SQL.
- **Decision:** `GET /health` and `GET /` return before `acquire()`. Health does not open Postgres and returns `{"ok": true}`. Catalog routes open a pooled connection on demand. `serve()` does not connect at startup.
- **Consequences:** Liveness stays cheap. The health body stays `{"ok": true}`. Do not open psycopg at import or from the register thread.

## Dual-stack IPv6 listen for Fly 6PN

- **Status:** accepted (2026-09-01)
- **Context:** Fly private networking delivers traffic over IPv6. Local clients often connect over IPv4. Binding a single family drops one of those paths.
- **Decision:** Listen dual-stack IPv6. `listen_host()` returns `::`, and the socket sets `IPV6_V6ONLY` to 0 so IPv4-mapped clients are accepted on the same socket.
- **Consequences:** One listen address serves Fly 6PN and local IPv4. An IPv4-only bind (`0.0.0.0`) drops private-network traffic.

## Views-only SQL

- **Status:** accepted (2026-08-28)
- **Context:** The CMS publishes `v1_*` views as the read contract. Ash resource tables are storage inside the CMS and are not a stable API.
- **Decision:** Read speakers, sponsors, years, talks, and sponsorships through `v1_speakers`, `v1_sponsors`, `v1_years`, `v1_talks`, `v1_sponsorships`, and `v1_year_sponsors`. SQL is read-only. Ash table names stay out of statements.
- **Consequences:** A CMS storage change shows up here only when a view changes. The HTTP contract remains the CMS OpenAPI. Handler tests use a fake catalog and do not need a database.

## Quality gates: pre-commit and one Gitea job per check

- **Status:** accepted (2026-09-15, script coverage widened 2026-09-22)
- **Context:** Every change needs tests, lint, SAST, a locked dependency audit, and secret scanning. One serial script repeats setup and hides which check failed.
- **Decision:** Local pre-commit runs tests (`scripts/run_tests.sh`), Ruff, Bandit, locked pip-audit (`scripts/pip_audit_locked.sh` via `uv export`), and gitleaks. Gitea (`.gitea/workflows/precommit.yml`) prepares the toolchain once, then runs one Gitea job per check in parallel.
- **Consequences:** A new check is a pre-commit hook plus a parallel job that restores the prepared environment. Check jobs do not run `uv sync` again. Bandit and Ruff cover `app.py` and `scripts`.
