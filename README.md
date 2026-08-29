# carolina-codes-python

Read-only v1 polyglot API for Carolina Code Conference. Queries `v1_*` SQL views.

```
DATABASE_URL=postgres://postgres:postgres@127.0.0.1:5432/carolina_dev \
CAROLINA_URL=http://127.0.0.1:4000 \
POLYGLOT_REGISTER_TOKEN=dev \
PUBLIC_BASE_URL=http://127.0.0.1:4004 \
PORT=4004 \
uv run python app.py
```
