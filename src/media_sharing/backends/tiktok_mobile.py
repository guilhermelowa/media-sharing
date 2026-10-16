"""TikTok publisher via the official Android app (ADB + Maestro).

Last fallback after ``tiktok_playwright`` and ``tiktok_api``. Drives the real app
on a shared USB-connected phone: the media file is pushed with ``adb`` and a
user-calibrated Maestro flow picks it, types the caption and taps Post.

Calibrate the flow with ``media-sharing inspect <account>`` + ``maestro studio``;
see ``config/mobile/tiktok.yaml`` and ``docs/mobile.md``. No login/session file is
stored — the app stays logged in on the phone.
"""

from __future__ import annotations

from ..models import Platform
from .base import Capabilities
from .mobile import MobilePublisher

DEFAULTS = {
    "app_id": "com.zhiliaoapp.musically",   # region variants exist; verify on device
    "flow": "tiktok.yaml",
    "media_dir": "/sdcard/Download/media_sharing",
    "caption_max_len": 2200,
    "step_timeout_ms": 120_000,
}


class TikTokMobilePublisher(MobilePublisher):
    name = "tiktok_mobile"
    DEFAULTS = DEFAULTS
    capabilities = Capabilities(
        platforms=(Platform.TIKTOK,),
        media_types=("video", "image"),
        needs_public_url=False,
        stores_session=False,
    )
