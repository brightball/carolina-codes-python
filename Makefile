PYTHON_SOURCES = app.py perf_test.py test_handlers.py test_quality_gates.py

.PHONY: test lint sast audit gitleaks hooks

test:
	./scripts/run_tests.sh

lint:
	uv run ruff check $(PYTHON_SOURCES)
	uv run ruff format --check $(PYTHON_SOURCES)

sast:
	uv run bandit -c pyproject.toml -r app.py

audit:
	./scripts/pip_audit_locked.sh

gitleaks:
	gitleaks git --redact --verbose --exit-code 1

hooks:
	pre-commit install
