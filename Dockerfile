FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.11.21 /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml uv.lock ./
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PORT=8080 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:${PATH}"
RUN uv sync --frozen --no-dev --no-install-project --no-cache
COPY app.py ./
RUN python -m compileall -q app.py
EXPOSE 8080
CMD ["python", "app.py"]
