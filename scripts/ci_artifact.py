#!/usr/bin/env python3
"""Upload and download Gitea Actions artifacts without Node.

Talks the v3 Actions artifact protocol that Gitea exposes at
/api/actions_pipeline/_apis/pipelines/workflows/{run_id}/artifacts.
Used by the prepare job (repo is already cloned) and by check jobs after
they fetch this file with the job token.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

CHUNK_SIZE = 8 * 1024 * 1024
API_VERSION = "6.0-preview"


def _env_get(env, *names, required=True):
    for name in names:
        value = env.get(name)
        if value:
            return value
    if required:
        joined = ", ".join(names)
        raise SystemExit(f"missing required environment: {joined}")
    return ""


def runtime_url(env) -> str:
    url = env.get("ACTIONS_RUNTIME_URL")
    if url:
        return url if url.endswith("/") else url + "/"
    server = _env_get(env, "GITHUB_SERVER_URL", "GITEA_SERVER_URL")
    return server.rstrip("/") + "/api/actions_pipeline/"


def runtime_token(env) -> str:
    return _env_get(env, "ACTIONS_RUNTIME_TOKEN", "GITHUB_TOKEN", "GITEA_TOKEN")


def run_id(env) -> str:
    return _env_get(env, "GITHUB_RUN_ID", "GITEA_RUN_ID")


def resolve_url(url: str, env) -> str:
    """Rebase API URLs onto ACTIONS_RUNTIME_URL's origin.

    Gitea often returns ROOT_URL hosts that job containers cannot reach.
    """
    if not url:
        raise SystemExit("artifact API returned an empty URL")
    runtime = runtime_url(env)
    parsed = urllib.parse.urlparse(url)
    rt = urllib.parse.urlparse(runtime)
    if not parsed.scheme:
        base = runtime if runtime.endswith("/") else runtime + "/"
        return urllib.parse.urljoin(base, url.lstrip("/"))
    return urllib.parse.urlunparse(
        (
            rt.scheme,
            rt.netloc,
            parsed.path,
            parsed.params,
            parsed.query,
            parsed.fragment,
        )
    )


def artifacts_url(env) -> str:
    return (
        f"{runtime_url(env)}_apis/pipelines/workflows/{run_id(env)}"
        f"/artifacts?api-version={API_VERSION}"
    )


def _request(method: str, url: str, env, data=None, headers=None, timeout=600):
    scheme = urllib.parse.urlparse(url).scheme
    if scheme not in {"http", "https"}:
        raise SystemExit(f"refusing artifact URL scheme: {scheme or 'empty'}")
    hdrs = {"Authorization": f"Bearer {runtime_token(env)}"}
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, data=data, method=method, headers=hdrs)
    try:
        return urllib.request.urlopen(req, timeout=timeout)  # nosec B310
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        exc.close()
        raise SystemExit(f"{method} {url} failed: {exc.code} {body}") from exc


def _read_json(resp):
    return json.load(resp)


def upload_artifact(name: str, path: str, env) -> None:
    source = os.fspath(path)
    size = os.path.getsize(source)
    if size < 1:
        raise SystemExit(f"refusing to upload empty artifact: {source}")
    create_headers = {"Content-Type": "application/json"}
    payload = json.dumps({"Type": "actions_storage", "Name": name, "RetentionDays": 1}).encode()
    with _request("POST", artifacts_url(env), env, data=payload, headers=create_headers) as resp:
        created = _read_json(resp)
    upload_base = resolve_url(
        created.get("fileContainerResourceUrl") or created.get("fileContainerResourceURL") or "",
        env,
    )
    filename = os.path.basename(source)
    item = urllib.parse.quote(f"{name}/{filename}", safe="")
    sep = "&" if "?" in upload_base else "?"
    put_url = f"{upload_base}{sep}itemPath={item}"
    chunk_size = int(env.get("CI_ARTIFACT_CHUNK_SIZE") or CHUNK_SIZE)
    sent = 0
    with open(source, "rb") as fh:
        while sent < size:
            chunk = fh.read(chunk_size)
            if not chunk:
                break
            end = sent + len(chunk) - 1
            digest = hashlib.md5(chunk, usedforsecurity=False).digest()
            md5_b64 = base64.b64encode(digest).decode("ascii")
            put_headers = {
                "Content-Type": "application/octet-stream",
                "Content-Range": f"bytes {sent}-{end}/{size}",
                "x-tfs-filelength": str(size),
                "x-actions-results-md5": md5_b64,
            }
            with _request("PUT", put_url, env, data=chunk, headers=put_headers):
                pass
            sent += len(chunk)
    confirm = artifacts_url(env) + f"&artifactName={urllib.parse.quote(name)}"
    with _request("PATCH", confirm, env):
        pass


def download_artifact(name: str, dest: str, env) -> None:
    with _request("GET", artifacts_url(env), env) as resp:
        listing = _read_json(resp)
    items = listing.get("value") or []
    match = next((item for item in items if item.get("name") == name), None)
    if match is None:
        names = [item.get("name") for item in items]
        raise SystemExit(f"artifact {name!r} not found; have {names}")
    container = resolve_url(
        match.get("fileContainerResourceUrl") or match.get("fileContainerResourceURL") or "",
        env,
    )
    sep = "&" if "?" in container else "?"
    files_url = f"{container}{sep}itemPath={urllib.parse.quote(name, safe='')}"
    with _request("GET", files_url, env) as resp:
        files = _read_json(resp)
    entries = files.get("value") or []
    if not entries:
        raise SystemExit(f"artifact {name!r} has no files")
    entry = next(
        (row for row in entries if str(row.get("path", "")).endswith(".tar.gz")),
        entries[0],
    )
    location = resolve_url(entry.get("contentLocation") or "", env)
    with _request("GET", location, env) as resp:
        blob = resp.read()
    dest_path = os.fspath(dest)
    parent = os.path.dirname(dest_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(dest_path, "wb") as fh:
        fh.write(blob)


def main(argv=None, env=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    environ = os.environ if env is None else env
    if len(args) != 3 or args[0] not in {"upload", "download"}:
        print("usage: ci_artifact.py upload|download NAME FILE", file=sys.stderr)
        return 2
    action, name, path = args
    if action == "upload":
        upload_artifact(name, path, environ)
    else:
        download_artifact(name, path, environ)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
