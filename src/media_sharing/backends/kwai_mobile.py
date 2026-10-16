"""Kwai publisher via the official Android app (ADB + Maestro).

Kwai has no usable web upload flow (Kwai Studio is live-streaming only), so this
backend drives the real app on a shared USB-connected phone. The media file is
pushed with ``adb`` and a user-calibrated Maestro flow picks it, types the
caption and taps Publish.

Calibrate the flow with ``media-sharing inspect <account>`` + ``maestro studio``;
see ``config/mobile/kwai.yaml`` and ``docs/mobile.md``. No login/session file is
stored — the app stays logged in on the phone.
"""

from __future__ import annotations

from ..models import Platform
from .base import Capabilities
from .mobile import MobilePublisher

DEFAULTS = {
    "app_id": "com.kwai.video",
    "flow": "kwai.yaml",
    "media_dir": "/sdcard/Download/media_sharing",
    "caption_max_len": 2000,
    "step_timeout_ms": 120_000,
}


class KwaiMobilePublisher(MobilePublisher):
    name = "kwai_mobile"
    DEFAULTS = DEFAULTS
    capabilities = Capabilities(
        platforms=(Platform.KWAI,),
        media_types=("video", "image"),
        needs_public_url=False,
        stores_session=False,
    )
