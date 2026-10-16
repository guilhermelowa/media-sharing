"""Domain models shared across the pipeline and publisher backends.

These types are the stable contract between the watcher/orchestrator and the
per-platform publisher implementations. The other agent implements publishers
against ``MediaItem`` / ``PostMetadata`` -> ``PublishResult``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from pathlib import Path

from pydantic import BaseModel, Field


class Platform(str, Enum):
    BLUESKY = "bluesky"
    X = "x"
    TIKTOK = "tiktok"
    KWAI = "kwai"
    # Instagram is intentionally deferred; see docs/plans/future_plans.md
    INSTAGRAM = "instagram"


class MediaType(str, Enum):
    IMAGE = "image"
    VIDEO = "video"
    UNKNOWN = "unknown"


class PostStatus(str, Enum):
    PENDING = "pending"
    PUBLISHED = "published"
    FAILED = "failed"
    SKIPPED = "skipped"          # already published (idempotency) or disabled
    MANUAL_REQUIRED = "manual_required"  # e.g. unaudited TikTok draft / Kwai captcha


class MediaItem(BaseModel):
    path: Path
    sha256: str
    media_type: MediaType = MediaType.UNKNOWN
    size_bytes: int = 0
    mime_type: str | None = None

    @property
    def filename(self) -> str:
        return self.path.name


class PostOverride(BaseModel):
    """Per-platform caption/options supplied via the sidecar JSON."""

    caption: str | None = None
    hashtags: list[str] = Field(default_factory=list)
    options: dict = Field(default_factory=dict)  # backend-specific (privacy, etc.)


class PostMetadata(BaseModel):
    """Everything a publisher needs to build a post for one account.

    ``caption``/``hashtags`` are the resolved values after applying the
    platform override (see ``pipeline._resolve_metadata``).
    """

    caption: str = ""
    hashtags: list[str] = Field(default_factory=list)
    options: dict = Field(default_factory=dict)
    scheduled_at: datetime | None = None


class Account(BaseModel):
    """A single social account owned by the operator."""

    name: str
    platform: Platform
    backend: str | None = None          # pin a specific backend, else use chain
    credentials: dict[str, str] = Field(default_factory=dict)
    enabled: bool = True
    options: dict = Field(default_factory=dict)


class PublishResult(BaseModel):
    platform: Platform
    backend: str
    status: PostStatus
    post_id: str | None = None
    post_url: str | None = None
    detail: str | None = None
    published_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    # Browser verification is deferred (see docs/plans/future_plans.md); the field
    # is forward-compatible scaffolding. None = not verified / unknown.
    verified: bool | None = None


class Job(BaseModel):
    """A unit of work: one media file -> one account."""

    media: MediaItem
    account: Account
    metadata: PostMetadata
    attempts: int = 0
