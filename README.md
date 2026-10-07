# carolina-codes-python

Read-only v1 polyglot API for Carolina Code Conference. Queries `v1_*` SQL views.

## Runtime

- Language: Python `>=3.11` (`requires-python` in `pyproject.toml`). The container image is `python:3.12-slim`.
- Framework: stdlib `http.server`. There is no separate framework package. The framework version is that Python version.
- Database client: `psycopg[binary]>=3.2` (psycopg 3).
- Installer: uv, image pin `0.11.21` (`ghcr.io/astral-sh/uv:0.11.21`). The image installs from `uv.lock` with `uv sync --frozen --no-dev`.
- Quality-gate tools, in the dev group and pre-commit, excluded from the runtime image: Ruff, Bandit, pip-audit, pre-commit, and gitleaks.

## Run

```
DATABASE_URL=postgres://postgres:postgres@127.0.0.1:5432/carolina_dev \
CAROLINA_URL=http://127.0.0.1:4000 \
POLYGLOT_REGISTER_TOKEN=dev \
PUBLIC_BASE_URL=http://127.0.0.1:4004 \
PORT=4004 \
uv run python app.py
```

## Quality gates

```
uv sync --group dev
pre-commit install
pre-commit run --all-files
```

Five checks run locally via pre-commit. Gitea Actions (`.gitea/workflows/precommit.yml`) prepares the environment once, then runs those same checks as parallel jobs:

| Check | Command |
| --- | --- |
| tests | `scripts/run_tests.sh` (`perf_test.py` + unittest; no live Postgres) |
| linter/formatter | `uv run ruff check .` and `uv run ruff format --check .` |
| SAST | `uv run bandit -c pyproject.toml -r app.py scripts` |
| dependency audit | `scripts/pip_audit_locked.sh` (uv.lock via `uv export`) |
| secrets | `gitleaks git --redact --verbose` |

The documented local CMS DSN `postgres://postgres:postgres@127.0.0.1:5432/carolina_dev` is allowlisted in `.gitleaks.toml`.
