"""Instagram publisher via the official Android app (ADB + Maestro).

The API path requires a public URL + Business account + app review, so this is
the **only** Instagram backend: it drives the real app on a shared USB-connected
phone. The media file is pushed with ``adb`` and a user-calibrated Maestro flow
picks it, types the caption and shares it.

Calibrate the flow with ``media-sharing inspect <account>`` + ``maestro studio``;
see ``config/mobile/instagram.yaml`` and ``docs/mobile.md``. No login/session file
is stored — the app stays logged in on the phone (accounts may be switched
between runs).
"""

from __future__ import annotations

from ..models import Platform
from .base import Capabilities
from .mobile import MobilePublisher

DEFAULTS = {
    "app_id": "com.instagram.android",
    "flow": "instagram.yaml",
    "media_dir": "/sdcard/Download/media_sharing",
    "caption_max_len": 2200,
    "step_timeout_ms": 180_000,
}


class InstagramMobilePublisher(MobilePublisher):
    name = "instagram_mobile"
    DEFAULTS = DEFAULTS
    capabilities = Capabilities(
        platforms=(Platform.INSTAGRAM,),
        media_types=("video", "image"),
        needs_public_url=False,
        stores_session=False,
    )
