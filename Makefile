.PHONY: test lint sast audit gitleaks hooks

test:
	./scripts/run_tests.sh

lint:
	uv run ruff check .
	uv run ruff format --check .

sast:
	uv run bandit -c pyproject.toml -r app.py scripts

audit:
	./scripts/pip_audit_locked.sh

gitleaks:
	gitleaks git --redact --verbose --exit-code 1

hooks:
	pre-commit install
