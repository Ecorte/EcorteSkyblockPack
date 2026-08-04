#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import shutil
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO = os.environ.get("GITHUB_REPO", "Ecorte/EcorteSkyblockPack")
HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "8080"))
CACHE_DIR = Path(os.environ.get("CACHE_DIR", Path(__file__).resolve().parent / "cache"))
REFRESH_SECONDS = int(os.environ.get("REFRESH_SECONDS", "300"))
USER_AGENT = os.environ.get("USER_AGENT", "EcorteSkyblockPack-mrpack-server")

_lock = threading.Lock()
_state: dict = {
    "tag": None,
    "name": None,
    "path": None,
    "size": None,
    "url": None,
    "updated_at": None,
    "error": None,
}


def _github_json(url: str) -> dict:
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": USER_AGENT,
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _download(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=600) as resp, open(tmp, "wb") as out:
        shutil.copyfileobj(resp, out, length=1024 * 1024)
    tmp.replace(dest)


def refresh(force: bool = False) -> None:
    with _lock:
        try:
            release = _github_json(f"https://api.github.com/repos/{REPO}/releases/latest")
            assets = [
                a
                for a in release.get("assets", [])
                if str(a.get("name", "")).lower().endswith(".mrpack")
            ]
            if not assets:
                raise RuntimeError(f"No .mrpack asset on latest release {release.get('tag_name')}")

            asset = max(assets, key=lambda a: a.get("created_at") or a.get("updated_at") or "")
            tag = release["tag_name"]
            name = asset["name"]
            url = asset["browser_download_url"]
            size = asset.get("size")
            path = CACHE_DIR / tag / name

            if path.exists() and not force and _state.get("tag") == tag and _state.get("path") == str(path):
                _state["error"] = None
                _state["updated_at"] = time.time()
                return

            if not path.exists() or force:
                print(f"Downloading {name} ({tag})...", flush=True)
                _download(url, path)
                print(f"Cached {path} ({path.stat().st_size} bytes)", flush=True)

            for old in CACHE_DIR.iterdir() if CACHE_DIR.exists() else []:
                if old.is_dir() and old.name != tag:
                    shutil.rmtree(old, ignore_errors=True)

            _state.update(
                {
                    "tag": tag,
                    "name": name,
                    "path": str(path),
                    "size": size or path.stat().st_size,
                    "url": url,
                    "updated_at": time.time(),
                    "error": None,
                }
            )
        except Exception as exc:
            _state["error"] = str(exc)
            print(f"Refresh failed: {exc}", flush=True)


def _refresh_loop() -> None:
    while True:
        time.sleep(REFRESH_SECONDS)
        refresh()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args) -> None:
        print(f"{self.address_string()} - {fmt % args}", flush=True)

    def _send(self, code: int, body: bytes, content_type: str, headers: dict | None = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        if headers:
            for key, value in headers.items():
                self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0].rstrip("/") or "/"

        if path in ("/health", "/healthz"):
            ok = _state.get("path") and not _state.get("error")
            payload = json.dumps({"ok": bool(ok), "tag": _state.get("tag"), "error": _state.get("error")}).encode()
            self._send(200 if ok else 503, payload, "application/json")
            return

        if path in ("/meta", "/info"):
            payload = json.dumps(
                {
                    "repo": REPO,
                    "tag": _state.get("tag"),
                    "name": _state.get("name"),
                    "size": _state.get("size"),
                    "source_url": _state.get("url"),
                    "updated_at": _state.get("updated_at"),
                    "error": _state.get("error"),
                    "download": "/latest.mrpack",
                },
                indent=2,
            ).encode()
            self._send(200 if _state.get("path") else 503, payload, "application/json")
            return

        if path in ("/", "/latest", "/latest.mrpack", "/pack.mrpack"):
            refresh()
            file_path = _state.get("path")
            name = _state.get("name") or "pack.mrpack"
            if not file_path or not Path(file_path).exists():
                msg = (_state.get("error") or "Latest mrpack is not available yet").encode()
                self._send(503, msg, "text/plain; charset=utf-8")
                return

            data_path = Path(file_path)
            size = data_path.stat().st_size
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(size))
            self.send_header("Content-Disposition", f'attachment; filename="{name}"')
            self.send_header("X-Pack-Tag", str(_state.get("tag") or ""))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            with open(data_path, "rb") as fh:
                shutil.copyfileobj(fh, self.wfile, length=1024 * 1024)
            return

        self._send(404, b"Not found\n", "text/plain; charset=utf-8")


def main() -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Fetching latest mrpack for {REPO}...", flush=True)
    refresh()
    threading.Thread(target=_refresh_loop, name="refresh", daemon=True).start()
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"Serving latest mrpack on http://{HOST}:{PORT}/latest.mrpack", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
