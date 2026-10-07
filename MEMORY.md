# Memory

Short index of non-obvious facts for agents in this repo. Durable choices stay in [DECISIONS.md](DECISIONS.md). Append a note here when a repeated operational gotcha appears. Do not store credentials, tokens, or private hostnames.

## Tests use a fake catalog

`test_handlers.py` and `perf_test.py` drive the shipped handlers with stand-in query and connect functions. `scripts/run_tests.sh` and `python -m unittest` need no Postgres. Live HTTP against the views needs the CMS database (Postgres 16) and `DATABASE_URL`.

## Local port 4004 versus container port 8080

The README command sets `PORT=4004`. The image and Fly set `PORT=8080` (`EXPOSE 8080`, Fly `internal_port = 8080`). Check the process on the port it was started with.

## `sslmode=disable` is appended when missing

`dsn()` appends `sslmode=disable` when `DATABASE_URL` has no `sslmode` parameter. Local CMS Postgres speaks plain TCP. A URL that omits the parameter makes the client negotiate TLS.

## Register must not open psycopg

`register()` runs once on a daemon thread. It returns when `CAROLINA_URL` or `POLYGLOT_REGISTER_TOKEN` is missing, and it logs and returns when the POST fails. The function must not open psycopg (`psycopg.connect`, `open_connection`, `open_pool`, or `db_query`). `GET /health` and `GET /` return before any connection. `perf_test.py` reads the source of `register` to keep it that way.

## The documented local DSN is gitleaks-allowlisted

`.gitleaks.toml` allowlists this line:

`postgres://postgres:postgres@127.0.0.1:5432/carolina_dev`

That is the local CMS example in the README and the default in `dsn()`. A Postgres URL with any other user or password fails gitleaks. Keep other database URLs in the environment.
