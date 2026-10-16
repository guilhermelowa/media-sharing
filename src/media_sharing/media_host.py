"""Transient local media host.

Some platforms (Instagram images, TikTok PULL_FROM_URL, aggregators that take
media URLs) require the media to be fetchable over HTTP. This serves files from
the local disk on a random port under an unguessable token path, so nothing is
uploaded to a third party. For platforms that need a public URL, put a tunnel
(cloudflared/ngrok) in front and set MEDIA_PUBLIC_BASE_URL.

Only Instagram's API path is known to require this today; it is therefore a
utility kept ready for docs/plans/future_plans.md.
"""

from __future__ import annotations

import http.server
import secrets
import threading
from pathlib import Path


class _Handler(http.server.SimpleHTTPRequestHandler):
    token: str = ""

    def translate_path(self, path: str) -> str:
        # Only serve under /<token>/ to keep the surface unguessable.
        prefix = f"/{self.token}/"
        if not path.startswith(prefix):
            return "/nonexistent"
        rel = path[len(prefix):]
        return str(self.directory / rel)  # type: ignore[attr-defined]

    def log_message(self, *args):  # silence
        pass


class TransientMediaHost:
    def __init__(self, base_dir: Path, public_base_url: str | None = None):
        self.base_dir = Path(base_dir)
        self.public_base_url = public_base_url
        self.token = secrets.token_urlsafe(16)
        self._server: http.server.ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        handler = type("BoundHandler", (_Handler,), {"token": self.token})
        self._server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self._server.directory = str(self.base_dir)  # type: ignore[attr-defined]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    @property
    def port(self) -> int:
        assert self._server is not None
        return self._server.server_address[1]

    def url_for(self, filename: str) -> str:
        base = self.public_base_url or f"http://127.0.0.1:{self.port}"
        return f"{base}/{self.token}/{filename}"

    def stop(self) -> None:
        if self._server:
            self._server.shutdown()
            self._server.server_close()
