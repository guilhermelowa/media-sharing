"""Orchestration: media + account -> ordered backends -> retries -> state.

The pipeline is backend-agnostic. It resolves per-platform metadata, walks the
fallback chain from ``config/platforms.yaml``, honours rate limits and
idempotency, and records every outcome in SQLite.
"""

from __future__ import annotations

import json
import logging
import time

from . import backends
from .backends.base import Publisher, PublishError
from .config import Settings
from .models import (
    Account,
    Job,
    MediaItem,
    PostMetadata,
    PostStatus,
    PublishResult,
)
from .state import StateStore

log = logging.getLogger("media_sharing.pipeline")

MAX_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = 5


def load_sidecar(media: MediaItem) -> dict:
    """Read ``<file>.json`` next to the media file, if present."""
    sidecar = media.path.with_suffix(media.path.suffix + ".json")
    if sidecar.exists():
        try:
            return json.loads(sidecar.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            log.warning("Bad sidecar %s: %s", sidecar, exc)
    return {}


def resolve_metadata(media: MediaItem, account: Account) -> PostMetadata:
    """Merge sidecar defaults with this account/platform overrides."""
    data = load_sidecar(media)
    default = data.get("default", {})
    override = (data.get("overrides", {}) or {}).get(account.platform.value, {})
    merged = {**default, **override}
    options = {**default.get("options", {}), **override.get("options", {})}
    return PostMetadata(
        caption=merged.get("caption", ""),
        hashtags=merged.get("hashtags", []),
        options=options,
    )


def backend_chain(settings: Settings, account: Account) -> list[str]:
    if account.backend:
        return [account.backend]
    policy = settings.platforms.get(account.platform.value)
    return list(policy.backends) if policy else []


def _run_with_retries(publisher: Publisher, media: MediaItem,
                      meta: PostMetadata) -> PublishResult:
    """Call publish, retrying transient failures with exponential backoff."""
    attempt = 0
    while True:
        try:
            return publisher.publish(media, meta)
        except PublishError as exc:
            if not exc.retryable or attempt >= MAX_ATTEMPTS:
                raise
            wait = RETRY_BACKOFF_SECONDS * (2 ** attempt)
            attempt += 1
            log.info("retrying %s in %ss (%s)", publisher.name, wait, exc)
            time.sleep(wait)


def publish_job(job: Job, settings: Settings, store: StateStore) -> PublishResult:
    media, account = job.media, job.account
    platform = account.platform

    if store.already_published(media, account.name, platform):
        log.info("skip (already published): %s -> %s", media.filename, account.name)
        return PublishResult(platform=platform, backend="-", status=PostStatus.SKIPPED)

    policy = settings.platforms.get(platform.value)
    daily_cap = policy.daily_cap if policy else None
    min_interval = policy.min_interval_seconds if policy else 0
    if not store.check_rate_limit(account.name, platform,
                                  daily_cap=daily_cap,
                                  min_interval_seconds=min_interval):
        log.info("rate-limited: %s -> %s", media.filename, account.name)
        return PublishResult(platform=platform, backend="-", status=PostStatus.SKIPPED,
                             detail="rate limit")

    chain = backend_chain(settings, account)
    if not chain:
        result = PublishResult(platform=platform, backend="-", status=PostStatus.FAILED,
                               detail="no backend configured")
        store.record(media, account.name, platform, "-", result.status, detail=result.detail)
        return result

    last_error = "no backends attempted"
    for name in chain:
        try:
            publisher = backends.build(name, account, settings)
            publisher.prepare()
            result = _run_with_retries(publisher, media, job.metadata)
            if result.status is PostStatus.PUBLISHED:
                store.record_post(account.name, platform)
            store.record(media, account.name, platform, name, result.status,
                         post_id=result.post_id, post_url=result.post_url,
                         detail=result.detail)
            return result
        except NotImplementedError:
            last_error = f"{name}: not implemented"
            log.warning("backend %s not implemented, trying next", name)
        except PublishError as exc:
            last_error = f"{name}: {exc}"
            log.warning("backend %s failed for %s: %s", name, account.name, exc)

    store.record(media, account.name, platform, ",".join(chain),
                 PostStatus.FAILED, detail=last_error)
    return PublishResult(platform=platform, backend=",".join(chain),
                         status=PostStatus.FAILED, detail=last_error)


def fan_out(media: MediaItem, settings: Settings, store: StateStore,
            *, account_name: str | None = None) -> list[PublishResult]:
    """Publish one media item to every enabled account (or a single one)."""
    results = []
    for account in settings.accounts_for(name=account_name):
        job = Job(media=media, account=account,
                  metadata=resolve_metadata(media, account))
        results.append(publish_job(job, settings, store))
    return results
