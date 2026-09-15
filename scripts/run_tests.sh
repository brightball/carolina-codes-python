#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
uv run python perf_test.py
uv run python -m unittest discover -s . -p 'test_*.py'
