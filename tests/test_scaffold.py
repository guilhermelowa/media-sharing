"""Placeholder tests for the scaffold.

The implementing agent should add real tests per backend, e.g.:
    - pipeline: fallback chain, idempotency, rate limits (StateStore)
    - watcher: sha256 + media classification + file move on success/failure
    - each publisher: request construction mocked at the HTTP/client boundary
"""

from __future__ import annotations

from media_sharing import backends
from media_sharing.models import MediaType
from media_sharing.watcher import classify


def test_registry_has_expected_backends():
    names = set(backends.available())
    assert {"bluesky_api", "x_api", "x_playwright", "tiktok_api",
            "tiktok_playwright", "kwai_playwright", "aggregator"} <= names


def test_classify_media_types():
    from pathlib import Path

    assert classify(Path("a.jpg")) is MediaType.IMAGE
    assert classify(Path("a.mp4")) is MediaType.VIDEO
    assert classify(Path("a.txt")) is MediaType.UNKNOWN


def test_load_dotenv_populates_env(tmp_path, monkeypatch):
    from media_sharing.config import load_dotenv

    env = tmp_path / ".env"
    env.write_text(
        "# comment\n"
        "FOO_BSKY=hello\n"
        'QUOTED="a b"\n'
        "EXISTING=fromfile\n"
        "EMPTY=\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("EXISTING", "fromenv")
    monkeypatch.delenv("FOO_BSKY", raising=False)
    load_dotenv(env)
    import os

    assert os.environ["FOO_BSKY"] == "hello"
    assert os.environ["QUOTED"] == "a b"
    assert os.environ["EXISTING"] == "fromenv"  # existing wins
