"""Publisher backend interface.

A backend is a thin adapter that publishes ONE media item to ONE account on ONE
platform. The pipeline chooses which backend to use from the platform's fallback
chain (``config/platforms.yaml``).

To add a backend:
    1. Subclass ``Publisher`` and set a unique ``name``.
    2. Implement ``publish``.
    3. Register it in ``backends/registry.py``.

Raise ``PublishError`` with ``retryable=True`` for transient failures (network,
rate limit), or ``retryable=False`` for permanent ones (auth, validation) so the
pipeline can move on to the next backend in the chain.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from ..models import Account, MediaItem, PostMetadata, PublishResult


class PublishError(Exception):
    def __init__(self, message: str, *, retryable: bool = False, code: str | None = None):
        super().__init__(message)
        self.retryable = retryable
        self.code = code


@dataclass(frozen=True)
class Capabilities:
    """Declares what a backend can do; the pipeline uses this for routing."""

    platforms: tuple
    media_types: tuple
    needs_public_url: bool = False   # backend fetches media from a URL
    stores_session: bool = False     # browser/session-based auth


class Publisher(ABC):
    name: str = "unnamed"
    capabilities: Capabilities

    def __init__(self, account: Account, settings):
        self.account = account
        self.settings = settings

    @abstractmethod
    def publish(self, media: MediaItem, meta: PostMetadata) -> PublishResult:
        """Publish and return a result. Raise ``PublishError`` on failure."""

    def prepare(self) -> None:
        """Optional one-time setup (load session, refresh token)."""

    def healthcheck(self) -> bool:
        """Return True if the backend looks usable for this account."""
        return True
