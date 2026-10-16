"""Small shared helpers for publisher backends."""

from __future__ import annotations

from .models import MediaItem, PostMetadata


def normalize_hashtag(tag: str) -> str:
    tag = tag.strip()
    if not tag:
        return ""
    return tag if tag.startswith("#") else f"#{tag}"


def compose_caption(meta: PostMetadata, *, max_len: int | None = None,
                    hashtags: bool = True, truncate_suffix: str = "\u2026") -> str:
    """Join caption + hashtags into final post text, optionally truncated.

    Keeps platform character limits (Bluesky 300, X 280, etc.) in one place.
    """
    parts: list[str] = []
    if meta.caption and meta.caption.strip():
        parts.append(meta.caption.strip())
    if hashtags and meta.hashtags:
        tags = " ".join(t for t in map(normalize_hashtag, meta.hashtags) if t)
        if tags:
            parts.append(tags)
    text = "\n\n".join(parts)
    if max_len is not None and len(text) > max_len:
        keep = max_len - len(truncate_suffix)
        text = text[:keep].rstrip() + truncate_suffix
    return text


def is_video(media: MediaItem) -> bool:
    return media.media_type.value == "video" or (media.mime_type or "").startswith("video/")


def is_image(media: MediaItem) -> bool:
    return media.media_type.value == "image" or (media.mime_type or "").startswith("image/")
