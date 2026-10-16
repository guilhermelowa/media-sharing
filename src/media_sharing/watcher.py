"""Filesystem watcher: drop a file into media/inbox/<account>/ and it gets queued.

Each incoming file is hashed (sha256) and fanned out to its account's platform
backends. The file is moved to processed/ on success or failed/ otherwise.
"""

from __future__ import annotations

import hashlib
import logging
import mimetypes
import shutil
from pathlib import Path

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from .config import Settings
from .models import MediaItem, MediaType, PostStatus
from .pipeline import fan_out
from .state import StateStore

log = logging.getLogger("media_sharing.watcher")

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
VIDEO_EXT = {".mp4", ".mov", ".webm", ".mkv"}


def sha256_of(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def classify(path: Path) -> MediaType:
    ext = path.suffix.lower()
    if ext in IMAGE_EXT:
        return MediaType.IMAGE
    if ext in VIDEO_EXT:
        return MediaType.VIDEO
    return MediaType.UNKNOWN


def build_item(path: Path) -> MediaItem:
    return MediaItem(
        path=path,
        sha256=sha256_of(path),
        media_type=classify(path),
        size_bytes=path.stat().st_size,
        mime_type=mimetypes.guess_type(path.name)[0],
    )


class InboxHandler(FileSystemEventHandler):
    def __init__(self, settings: Settings, store: StateStore):
        self.settings = settings
        self.store = store

    def on_created(self, event):
        if event.is_directory:
            return
        path = Path(event.src_path)
        if path.suffix == ".json":  # sidecar handled with its media
            return
        self.process(path)

    def process(self, path: Path) -> None:
        # account name = relative subdirectory under inbox
        try:
            rel = path.relative_to(self.settings.paths.inbox)
        except ValueError:
            return
        account_name = rel.parts[0] if len(rel.parts) > 1 else None
        log.info("processing %s (account=%s)", path.name, account_name or "ALL")

        item = build_item(path)
        results = fan_out(item, self.settings, self.store, account_name=account_name)
        ok = any(r.status in (PostStatus.PUBLISHED, PostStatus.MANUAL_REQUIRED,
                              PostStatus.SKIPPED) for r in results)
        dest_root = self.settings.paths.processed if ok else self.settings.paths.failed
        self._move(path, dest_root)

    def _move(self, path: Path, dest_root: Path) -> None:
        dest_root.mkdir(parents=True, exist_ok=True)
        dest = dest_root / path.name
        try:
            shutil.move(str(path), str(dest))
        except Exception as exc:  # noqa: BLE001
            log.warning("could not move %s: %s", path, exc)


def run_watcher(settings: Settings, store: StateStore) -> None:
    settings.paths.inbox.mkdir(parents=True, exist_ok=True)
    handler = InboxHandler(settings, store)
    observer = Observer()
    observer.schedule(handler, str(settings.paths.inbox), recursive=True)
    observer.start()
    log.info("watching %s", settings.paths.inbox)
    try:
        while observer.is_alive():
            observer.join(1)
    finally:
        observer.stop()
        observer.join()
