"""Shared ADB + Maestro helpers for mobile-app publishers (Kwai, Instagram, TikTok).

Mobile backends drive the **official Android app** on a single USB-connected
phone: ``adb`` stages the media file on the device and Maestro runs a
user-calibrated UI flow that picks the file, types the caption and taps Publish.
There are **no Python dependencies** beyond the standard library — everything
goes through ``subprocess``.

Mirrors ``browser.py``: a ``MaestroConfig`` built from ``account.options`` (with
per-platform defaults), a ``publish_via_mobile`` entry point, and thin
per-platform modules. The ``Publisher`` contract and pipeline are unchanged.

Unlike browser backends, **no session file is created**: the app stays logged in
on the phone (accounts may be switched between runs).
"""

from __future__ import annotations

import contextlib
import fcntl
import logging
import shutil
import subprocess
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar

from ..models import Account, MediaItem, PostMetadata, PostStatus, PublishResult
from ..utils import compose_caption
from .base import Publisher, PublishError

log = logging.getLogger("media_sharing.mobile")

DEFAULT_MEDIA_DIR = "/sdcard/Download/media_sharing"


@dataclass
class MaestroConfig:
    app_id: str = ""
    flow: str = ""
    device: str | None = None            # adb serial; None = auto-select
    media_dir: str = DEFAULT_MEDIA_DIR   # on-device staging directory
    caption_max_len: int = 2200
    step_timeout_ms: int = 120_000
    # When true the flow leaves the post staged but unconfirmed (no Publish tap);
    # the backend reports MANUAL_REQUIRED instead of PUBLISHED.
    manual_publish: bool = False
    extra: dict = field(default_factory=dict)


def cfg(account: Account, defaults: dict, *, overrides: dict | None = None) -> MaestroConfig:
    """Merge account.options over per-platform defaults into a MaestroConfig.

    Unknown keys are kept in ``.extra`` so flows can pass arbitrary settings.
    """
    values = {**defaults, **(account.options or {}), **(overrides or {})}
    known = set(MaestroConfig.__dataclass_fields__)
    kwargs = {k: v for k, v in values.items() if k in known}
    extra = {k: v for k, v in values.items() if k not in known}
    config = MaestroConfig(**kwargs)
    config.extra = extra
    return config


# -- external tooling -------------------------------------------------------

_TOOL_HINTS = {
    "adb": "install Android platform-tools and put `adb` on PATH",
    "maestro": "install Maestro (https://maestro.mobile.dev) and put it on PATH",
}


def _require_tools(*names: str) -> None:
    missing = [name for name in names if shutil.which(name) is None]
    if missing:
        hints = "; ".join(_TOOL_HINTS.get(name, name) for name in missing)
        raise PublishError(
            f"missing mobile tooling: {', '.join(missing)}. {hints}. See docs/mobile.md.",
            retryable=False, code="missing_dependency",
        )


def require_tools() -> None:
    """Ensure both ``adb`` and ``maestro`` are available on PATH."""
    _require_tools("adb", "maestro")


def require_adb() -> None:
    _require_tools("adb")


def _run(argv: list, *, timeout: int | None = None) -> subprocess.CompletedProcess:
    log.debug("running: %s", " ".join(str(a) for a in argv))
    try:
        return subprocess.run(
            [str(a) for a in argv], check=False,
            capture_output=True, text=True, timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise PublishError(f"command not found: {argv[0]}", retryable=False,
                           code="missing_dependency") from exc
    except subprocess.TimeoutExpired as exc:
        raise PublishError(f"command timed out: {argv[0]}", retryable=True,
                           code="timeout") from exc


# -- devices ----------------------------------------------------------------

def list_devices() -> list[str]:
    """Return the serials of connected devices in the ``device`` state."""
    require_adb()
    result = _run(["adb", "devices"])
    devices: list[str] = []
    for line in result.stdout.splitlines()[1:]:
        line = line.strip()
        if not line or "\t" not in line:
            continue
        serial, state = line.split("\t", 1)
        if state.strip().split()[0:1] == ["device"]:
            devices.append(serial.strip())
    return devices


def resolve_device(config: MaestroConfig) -> str:
    """Pick the adb target: the configured serial, or the only connected one."""
    devices = list_devices()
    if config.device:
        if config.device not in devices:
            raise PublishError(
                f"configured device '{config.device}' not connected "
                f"(connected: {devices or 'none'})",
                retryable=False, code="no_device",
            )
        return config.device
    if not devices:
        raise PublishError(
            "no Android device connected (adb devices is empty); connect one over "
            "USB with debugging enabled",
            retryable=False, code="no_device",
        )
    if len(devices) > 1:
        raise PublishError(
            f"multiple devices connected ({', '.join(devices)}); set options.device "
            "to the intended serial",
            retryable=False, code="multiple_devices",
        )
    return devices[0]


def app_installed(pkg: str, device: str) -> bool:
    _require_tools("adb")
    result = _run(
        ["adb", "-s", device, "shell", "pm", "list", "packages", "--user", "current", pkg]
    )
    return f"package:{pkg}" in result.stdout


def launch_app(device: str, pkg: str) -> None:
    _require_tools("adb")
    _run(["adb", "-s", device, "shell", "monkey", "-p", pkg,
          "-c", "android.intent.category.LAUNCHER", "1"])


def wake_device(device: str) -> None:
    """Best-effort: wake the screen, dismiss the keyguard, keep awake on USB.

    A secure lock (PIN/pattern) cannot be dismissed programmatically — disable it
    for unattended runs. Never raises.
    """
    try:
        _require_tools("adb")
    except PublishError:
        return
    for args in (
        ["input", "keyevent", "KEYCODE_WAKEUP"],
        ["wm", "dismiss-keyguard"],
        ["svc", "power", "stayon", "usb"],
    ):
        with contextlib.suppress(PublishError):
            _run(["adb", "-s", device, "shell", *args])


def probe_handle(device: str, expected: str) -> bool | None:
    """Best-effort check that ``expected`` appears among the device's accounts.

    Returns True/False, or None when the probe cannot determine anything (so the
    caller should not treat it as a mismatch).
    """
    try:
        result = _run(["adb", "-s", device, "shell", "dumpsys", "account"])
    except PublishError:
        return None
    if not result.stdout.strip():
        return None
    return expected.lower() in result.stdout.lower()


# -- media staging ----------------------------------------------------------

def push_media(media: MediaItem, device: str, dest_dir: str) -> str:
    """Copy ``media`` to the device, returning its on-device path."""
    _require_tools("adb")
    dest = f"{dest_dir.rstrip('/')}/{media.filename}"
    _run(["adb", "-s", device, "shell", "mkdir", "-p", dest_dir])
    result = _run(["adb", "-s", device, "push", media.path, dest])
    if result.returncode != 0:
        raise PublishError(
            f"adb push failed: {result.stderr.strip() or result.stdout.strip()}",
            retryable=True, code="adb_push",
        )
    return dest


def dump_ui(device: str, path: Path) -> Path:
    """Dump the current UI hierarchy to ``path`` (best-effort)."""
    _require_tools("adb")
    result = _run(["adb", "-s", device, "exec-out", "uiautomator", "dump", "/dev/tty"])
    if result.returncode != 0 or not result.stdout.strip():
        _run(["adb", "-s", device, "shell", "uiautomator", "dump", "/sdcard/ui.xml"])
        result = _run(["adb", "-s", device, "exec-out", "cat", "/sdcard/ui.xml"])
    path = Path(path)
    path.write_text(result.stdout, encoding="utf-8")
    return path


def screenshot(device: str, path: Path) -> Path:
    """Capture the current screen to ``path`` as PNG."""
    _require_tools("adb")
    try:
        result = subprocess.run(
            ["adb", "-s", device, "exec-out", "screencap", "-p"],
            check=False, capture_output=True, timeout=30,
        )
    except subprocess.TimeoutExpired as exc:
        raise PublishError("adb screencap timed out", retryable=True,
                           code="timeout") from exc
    path = Path(path)
    path.write_bytes(result.stdout)
    return path


def index_media(on_device_path: str, device: str) -> bool:
    """Best-effort MediaStore rescan for the gallery fallback.

    ``MEDIA_SCANNER_SCAN_FILE`` is a no-op on Android 10+, hence the primary SAF
    "Files/Downloads" picker; this only helps older devices. Never raises.
    """
    _require_tools("adb")
    uri = f"file://{on_device_path}"
    try:
        _run(["adb", "-s", device, "shell", "am", "broadcast",
              "-a", "android.intent.action.MEDIA_SCANNER_SCAN_FILE", "-d", uri])
    except PublishError as exc:
        log.debug("media scan skipped: %s", exc)
        return False
    return True


# -- Maestro ----------------------------------------------------------------

def resolve_flow(settings, config: MaestroConfig) -> Path:
    """Locate the Maestro flow YAML (relative names resolve under config/mobile/)."""
    if not config.flow:
        raise PublishError("no Maestro flow configured (set options.flow)",
                           retryable=False, code="config")
    path = Path(config.flow)
    if not path.is_absolute():
        path = Path(settings.paths.mobile_flows) / path
    if not path.exists():
        raise PublishError(f"Maestro flow not found: {path}",
                           retryable=False, code="config")
    return path


def parse_junit(xml_path: Path) -> tuple[bool, str]:
    """Return ``(ok, detail)`` from a JUnit report (missing report => ok)."""
    xml_path = Path(xml_path)
    if not xml_path.exists():
        return True, "no report"
    try:
        root = ET.parse(xml_path).getroot()
    except ET.ParseError as exc:
        return False, f"unreadable report: {exc}"
    failures = errors = 0
    for suite in root.iter("testsuite"):
        failures += int(suite.get("failures", 0) or 0)
        errors += int(suite.get("errors", 0) or 0)
    if failures or errors:
        return False, f"{failures} failure(s), {errors} error(s)"
    return True, "ok"


def flow_result(exit_code: int, junit_path: Path) -> tuple[bool, str]:
    """Combine the Maestro exit code and JUnit report into success/failure."""
    report_ok, detail = parse_junit(junit_path)
    if exit_code != 0:
        return False, f"exit {exit_code}; {detail}"
    return report_ok, detail


def run_flow(config: MaestroConfig, device: str, env: dict, artifacts: Path) -> Path:
    """Run the Maestro flow; return the JUnit report path on success.

    Raises ``PublishError`` (retryable — UI flows are flaky) when the flow fails.
    """
    require_tools()
    artifacts = Path(artifacts)
    artifacts.mkdir(parents=True, exist_ok=True)
    junit = artifacts / "maestro.xml"
    argv: list = ["maestro", "--device", device, "test", config.flow]
    for key, value in env.items():
        argv += ["-e", f"{key}={value}"]
    argv += ["--format", "junit", "--output", junit, "--test-output-dir", artifacts]
    result = _run(argv)
    ok, detail = flow_result(result.returncode, junit)
    if not ok:
        tail = "\n".join(
            ((result.stdout or "") + (result.stderr or "")).strip().splitlines()[-15:]
        )
        log.error("maestro flow failed for %s (artifacts: %s)", config.flow, artifacts)
        raise PublishError(
            f"maestro flow failed ({detail}); artifacts: {artifacts}"
            + (f"\n{tail}" if tail else ""),
            retryable=True, code="flow_failed",
        )
    log.info("maestro flow ok; artifacts: %s", artifacts)
    return junit


# -- device lock ------------------------------------------------------------

@contextlib.contextmanager
def device_lock(settings):
    """Serialise access to the shared phone across processes (``state/mobile.lock``)."""
    lock_path = Path(settings.paths.state_db).parent / "mobile.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("w")
    try:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise PublishError(
                "another mobile run is using the device (state/mobile.lock held)",
                retryable=True, code="device_busy",
            ) from exc
        yield handle
    finally:
        with contextlib.suppress(Exception):
            fcntl.flock(handle, fcntl.LOCK_UN)
        handle.close()


# -- publishing -------------------------------------------------------------

def validate(account: Account, config: MaestroConfig) -> str:
    """Check tooling + device + app, returning the resolved device serial."""
    require_tools()
    if not config.app_id:
        raise PublishError(f"{account.platform.value}: no app_id configured",
                           retryable=False, code="config")
    device = resolve_device(config)
    if not app_installed(config.app_id, device):
        raise PublishError(
            f"{account.platform.value}: app '{config.app_id}' is not installed on "
            f"{device}",
            retryable=False, code="app_missing",
        )
    return device


def publish_via_mobile(settings, account: Account, config: MaestroConfig,
                       media: MediaItem, meta: PostMetadata) -> Path:
    """Stage ``media`` on the phone and drive the app's Maestro flow.

    Returns the artifacts directory (logs, screenshots and the Maestro report).
    """
    device = validate(account, config)
    config.flow = str(resolve_flow(settings, config))
    text = compose_caption(meta, max_len=config.caption_max_len)
    artifacts = Path(settings.paths.artifacts) / account.name / media.sha256

    with device_lock(settings):
        wake_device(device)
        on_device = push_media(media, device, config.media_dir)
        index_media(on_device, device)
        env = {"CAPTION": text, "MEDIA": media.filename, "MEDIA_PATH": on_device}
        run_flow(config, device, env, artifacts)
    return artifacts


class MobilePublisher(Publisher):
    """Base for app-driving publishers; subclasses set ``name``, ``DEFAULTS``,
    ``capabilities``."""

    name = "unnamed"
    DEFAULTS: ClassVar[dict] = {}

    def _config(self) -> MaestroConfig:
        return cfg(self.account, self.DEFAULTS)

    def healthcheck(self) -> bool:
        try:
            validate(self.account, self._config())
        except PublishError:
            return False
        return True

    def prepare(self) -> None:
        device = validate(self.account, self._config())
        expected = str(self.account.options.get("expected_handle") or "").strip()
        if expected and probe_handle(device, expected) is False:
            raise PublishError(
                f"{self.account.platform.value}: device account does not match "
                f"expected handle '{expected}' (switch accounts on the phone)",
                retryable=False, code="account_mismatch",
            )

    def publish(self, media: MediaItem, meta: PostMetadata) -> PublishResult:
        config = self._config()
        artifacts = publish_via_mobile(self.settings, self.account, config, media, meta)
        detail = f"artifacts: {artifacts}"
        if config.manual_publish:
            return PublishResult(
                platform=self.account.platform, backend=self.name,
                status=PostStatus.MANUAL_REQUIRED,
                detail=f"post ready — tap Publicar on the phone to confirm; {detail}",
            )
        return PublishResult(platform=self.account.platform, backend=self.name,
                             status=PostStatus.PUBLISHED, detail=detail)


__all__ = [
    "DEFAULT_MEDIA_DIR",
    "MaestroConfig",
    "MobilePublisher",
    "app_installed",
    "cfg",
    "device_lock",
    "dump_ui",
    "flow_result",
    "index_media",
    "launch_app",
    "list_devices",
    "parse_junit",
    "probe_handle",
    "publish_via_mobile",
    "push_media",
    "require_adb",
    "require_tools",
    "resolve_device",
    "resolve_flow",
    "run_flow",
    "screenshot",
    "validate",
    "wake_device",
]
