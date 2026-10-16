"""ADB + Maestro mobile backend tests. ``subprocess.run`` is mocked."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from media_sharing.backends import mobile
from media_sharing.backends.base import PublishError
from media_sharing.backends.instagram_mobile import InstagramMobilePublisher
from media_sharing.backends.kwai_mobile import KwaiMobilePublisher
from media_sharing.config import Paths, Settings
from media_sharing.models import (
    Account,
    MediaItem,
    MediaType,
    Platform,
    PostMetadata,
    PostStatus,
)
from media_sharing.state import StateStore


class FakeCompleted:
    def __init__(self, stdout: str = "", stderr: str = "", returncode: int = 0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


def fake_run_factory(responses):
    """Return (fake_run, calls). ``responses`` is [(substring, FakeCompleted)]."""
    calls: list[list[str]] = []

    def fake_run(argv, check=False, capture_output=True, text=True, timeout=None):
        parts = [str(a) for a in argv]
        calls.append(parts)
        joined = " ".join(parts)
        for needle, response in responses:
            if needle in joined:
                return response
        return FakeCompleted()

    return fake_run, calls


def make_media(tmp_path, name="clip.mp4", media_type=MediaType.VIDEO, mime="video/mp4"):
    p = tmp_path / name
    p.write_bytes(b"x" * 1024)
    return MediaItem(path=p, sha256="deadbeef", media_type=media_type,
                     size_bytes=1024, mime_type=mime)


def which_ok(name):
    return f"/usr/bin/{name}"


# --- dependency checks -----------------------------------------------------

def test_require_tools_missing(monkeypatch):
    monkeypatch.setattr(mobile.shutil, "which", lambda name: None)
    with pytest.raises(PublishError) as exc:
        mobile.require_tools()
    assert exc.value.code == "missing_dependency"
    assert exc.value.retryable is False


def test_require_tools_ok(monkeypatch):
    monkeypatch.setattr(mobile.shutil, "which", which_ok)
    mobile.require_tools()  # no raise


def test_cfg_merges_account_options():
    acct = Account(name="a", platform=Platform.KWAI,
                   options={"app_id": "com.x", "device": "SER", "custom": 1})
    cfg = mobile.cfg(acct, {"app_id": "default", "caption_max_len": 10})
    assert cfg.app_id == "com.x"
    assert cfg.device == "SER"
    assert cfg.caption_max_len == 10
    assert cfg.extra["custom"] == 1


# --- device resolution -----------------------------------------------------

def test_list_devices(monkeypatch):
    monkeypatch.setattr(mobile.shutil, "which", which_ok)
    run, calls = fake_run_factory([
        ("adb devices",
         FakeCompleted(stdout="List of devices attached\nSER1\tdevice\nSER2\tunauthorized\n")),
    ])
    monkeypatch.setattr(mobile.subprocess, "run", run)
    assert mobile.list_devices() == ["SER1"]
    assert calls == [["adb", "devices"]]


def test_resolve_device_single(monkeypatch):
    monkeypatch.setattr(mobile, "list_devices", lambda: ["SER1"])
    assert mobile.resolve_device(mobile.MaestroConfig()) == "SER1"


def test_resolve_device_none(monkeypatch):
    monkeypatch.setattr(mobile, "list_devices", list)
    with pytest.raises(PublishError) as exc:
        mobile.resolve_device(mobile.MaestroConfig())
    assert exc.value.code == "no_device"


def test_resolve_device_many(monkeypatch):
    monkeypatch.setattr(mobile, "list_devices", lambda: ["A", "B"])
    with pytest.raises(PublishError) as exc:
        mobile.resolve_device(mobile.MaestroConfig())
    assert exc.value.code == "multiple_devices"


def test_resolve_device_configured_missing(monkeypatch):
    monkeypatch.setattr(mobile, "list_devices", lambda: ["A"])
    with pytest.raises(PublishError) as exc:
        mobile.resolve_device(mobile.MaestroConfig(device="B"))
    assert exc.value.code == "no_device"


def test_app_installed(monkeypatch):
    monkeypatch.setattr(mobile.shutil, "which", which_ok)
    run, calls = fake_run_factory([
        ("pm list packages", FakeCompleted(stdout="package:com.kwai.video\n")),
    ])
    monkeypatch.setattr(mobile.subprocess, "run", run)
    assert mobile.app_installed("com.kwai.video", "SER") is True
    assert calls[0] == ["adb", "-s", "SER", "shell", "pm", "list", "packages",
                        "--user", "current", "com.kwai.video"]


# --- media staging ---------------------------------------------------------

def test_push_media_commands(monkeypatch, tmp_path):
    monkeypatch.setattr(mobile.shutil, "which", which_ok)
    run, calls = fake_run_factory([])
    monkeypatch.setattr(mobile.subprocess, "run", run)
    media = make_media(tmp_path)

    dest = mobile.push_media(media, "SER", "/sdcard/Download/media_sharing")

    assert dest == "/sdcard/Download/media_sharing/clip.mp4"
    assert calls[0] == ["adb", "-s", "SER", "shell", "mkdir", "-p",
                        "/sdcard/Download/media_sharing"]
    assert calls[1] == ["adb", "-s", "SER", "push", str(media.path), dest]


def test_push_media_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(mobile.shutil, "which", which_ok)
    run, _ = fake_run_factory([("push", FakeCompleted(returncode=1, stderr="boom"))])
    monkeypatch.setattr(mobile.subprocess, "run", run)
    with pytest.raises(PublishError) as exc:
        mobile.push_media(make_media(tmp_path), "SER", "/sdcard/Download")
    assert exc.value.code == "adb_push"
    assert exc.value.retryable is True


def test_index_media(monkeypatch):
    monkeypatch.setattr(mobile.shutil, "which", which_ok)
    run, calls = fake_run_factory([])
    monkeypatch.setattr(mobile.subprocess, "run", run)
    assert mobile.index_media("/sdcard/x/clip.mp4", "SER") is True
    assert calls[0][:6] == ["adb", "-s", "SER", "shell", "am", "broadcast"]
    assert "file:///sdcard/x/clip.mp4" in calls[0]


# --- Maestro flow ----------------------------------------------------------

def test_run_flow_command_and_success(monkeypatch, tmp_path):
    monkeypatch.setattr(mobile.shutil, "which", which_ok)
    flow = tmp_path / "flow.yaml"
    flow.write_text("appId: x\n---\n- tapOn: y\n")
    artifacts = tmp_path / "artifacts"
    captured = {}

    def fake_run(argv, **kwargs):
        parts = [str(a) for a in argv]
        captured["argv"] = parts
        out = parts[parts.index("--output") + 1]
        Path(out).write_text('<testsuite tests="1" failures="0" errors="0"></testsuite>')
        return FakeCompleted(returncode=0)

    monkeypatch.setattr(mobile.subprocess, "run", fake_run)
    cfg = mobile.MaestroConfig(app_id="com.kwai.video", flow=str(flow))

    junit = mobile.run_flow(cfg, "SER", {"CAPTION": "hi"}, artifacts)

    argv = captured["argv"]
    assert argv[:5] == ["maestro", "--device", "SER", "test", str(flow)]
    assert "CAPTION=hi" in argv
    assert argv[argv.index("--format") + 1] == "junit"
    assert "--output" in argv
    assert "--test-output-dir" in argv
    assert junit == artifacts / "maestro.xml"


def test_run_flow_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(mobile.shutil, "which", which_ok)
    flow = tmp_path / "flow.yaml"
    flow.write_text("appId: x\n")

    def fake_run(argv, **kwargs):
        parts = [str(a) for a in argv]
        Path(parts[parts.index("--output") + 1]).write_text(
            '<testsuite tests="1" failures="1" errors="0"></testsuite>')
        return FakeCompleted(returncode=1)

    monkeypatch.setattr(mobile.subprocess, "run", fake_run)
    with pytest.raises(PublishError) as exc:
        mobile.run_flow(mobile.MaestroConfig(flow=str(flow)), "SER", {},
                        tmp_path / "artifacts")
    assert exc.value.code == "flow_failed"
    assert exc.value.retryable is True


def test_flow_result_mapping(tmp_path):
    good = tmp_path / "good.xml"
    good.write_text('<testsuite tests="1" failures="0" errors="0"></testsuite>')
    bad = tmp_path / "bad.xml"
    bad.write_text('<testsuite tests="1" failures="1" errors="0"></testsuite>')

    assert mobile.flow_result(0, good)[0] is True
    assert mobile.flow_result(1, good)[0] is False
    assert mobile.flow_result(0, bad)[0] is False
    assert mobile.flow_result(0, tmp_path / "missing.xml")[0] is True

    broken = tmp_path / "broken.xml"
    broken.write_text("not xml")
    assert mobile.flow_result(0, broken)[0] is False


def test_parse_junit_testsuites_wrapper(tmp_path):
    report = tmp_path / "report.xml"
    report.write_text('<testsuites><testsuite failures="0" errors="1"/></testsuites>')
    ok, detail = mobile.parse_junit(report)
    assert ok is False
    assert "1 error" in detail


# --- device lock -----------------------------------------------------------

def test_device_lock_serializes(tmp_path):
    settings = Settings(paths=Paths(state_db=tmp_path / "state" / "state.db"))
    with (
        mobile.device_lock(settings),
        pytest.raises(PublishError) as exc,
        mobile.device_lock(settings),
    ):
        pass
    assert exc.value.code == "device_busy"


# --- Publisher integration -------------------------------------------------

def test_mobile_publisher_publish(monkeypatch, tmp_path):
    flow = tmp_path / "kwai.yaml"
    flow.write_text("appId: com.kwai.video\n---\n- tapOn: x\n")
    settings = Settings(paths=Paths(
        state_db=tmp_path / "state" / "state.db",
        mobile_flows=tmp_path,
        artifacts=tmp_path / "artifacts",
    ))
    pub = KwaiMobilePublisher(Account(name="acct", platform=Platform.KWAI), settings)
    media = make_media(tmp_path)
    calls: dict = {}

    monkeypatch.setattr(mobile, "validate", lambda acct, cfg: "SER")
    monkeypatch.setattr(mobile, "push_media",
                        lambda m, d, dest: "/sdcard/x/clip.mp4")
    monkeypatch.setattr(mobile, "index_media", lambda p, d: True)

    def fake_run_flow(cfg, device, env, artifacts):
        calls["env"] = env
        calls["flow"] = cfg.flow
        calls["artifacts"] = str(artifacts)

    monkeypatch.setattr(mobile, "run_flow", fake_run_flow)

    result = pub.publish(media, PostMetadata(caption="hello", hashtags=["ai"]))

    assert result.status is PostStatus.PUBLISHED
    assert result.backend == "kwai_mobile"
    assert result.platform is Platform.KWAI
    assert calls["flow"] == str(flow)
    assert "hello" in calls["env"]["CAPTION"]
    assert "#ai" in calls["env"]["CAPTION"]
    assert calls["env"]["MEDIA"] == "clip.mp4"
    assert calls["artifacts"].endswith("acct/deadbeef")


def test_mobile_publisher_manual_publish(monkeypatch, tmp_path):
    flow = tmp_path / "kwai.yaml"
    flow.write_text("appId: com.kwai.video\n---\n- tapOn: x\n")
    settings = Settings(paths=Paths(
        state_db=tmp_path / "state" / "state.db",
        mobile_flows=tmp_path,
        artifacts=tmp_path / "artifacts",
    ))
    acct = Account(name="acct", platform=Platform.KWAI,
                   options={"manual_publish": True})
    pub = KwaiMobilePublisher(acct, settings)
    media = make_media(tmp_path)

    monkeypatch.setattr(mobile, "validate", lambda acct, cfg: "SER")
    monkeypatch.setattr(mobile, "push_media", lambda m, d, dest: "/sdcard/x/clip.mp4")
    monkeypatch.setattr(mobile, "index_media", lambda p, d: True)
    artifacts = settings.paths.artifacts / "acct" / media.sha256
    monkeypatch.setattr(mobile, "run_flow",
                        lambda cfg, device, env, art: artifacts)

    result = pub.publish(media, PostMetadata(caption="hello"))

    assert result.status is PostStatus.MANUAL_REQUIRED
    assert "Publicar" in result.detail
    assert str(artifacts) in result.detail


def test_mobile_healthcheck(monkeypatch, tmp_path):
    settings = Settings(paths=Paths(state_db=tmp_path / "state.db"))
    pub = KwaiMobilePublisher(Account(name="a", platform=Platform.KWAI), settings)

    def boom(acct, cfg):
        raise PublishError("nope", code="app_missing")

    monkeypatch.setattr(mobile, "validate", boom)
    assert pub.healthcheck() is False
    monkeypatch.setattr(mobile, "validate", lambda acct, cfg: "SER")
    assert pub.healthcheck() is True


def test_mobile_prepare_account_mismatch(monkeypatch, tmp_path):
    settings = Settings(paths=Paths(state_db=tmp_path / "state.db"))
    acct = Account(name="a", platform=Platform.INSTAGRAM,
                   options={"expected_handle": "me"})
    pub = InstagramMobilePublisher(acct, settings)
    monkeypatch.setattr(mobile, "validate", lambda acct, cfg: "SER")

    monkeypatch.setattr(mobile, "probe_handle", lambda d, e: False)
    with pytest.raises(PublishError) as exc:
        pub.prepare()
    assert exc.value.code == "account_mismatch"

    monkeypatch.setattr(mobile, "probe_handle", lambda d, e: None)
    pub.prepare()  # inconclusive -> no failure


def test_registry_mobile_backends():
    from media_sharing import backends

    assert {"kwai_mobile", "tiktok_mobile", "instagram_mobile"} <= set(backends.available())
    assert "kwai_mobile" in backends.MOBILE_BACKENDS
    assert "kwai_mobile" not in backends.LOGIN_BACKENDS


# --- state migration -------------------------------------------------------

def _columns(db: Path) -> set[str]:
    conn = sqlite3.connect(db)
    try:
        return {row[1] for row in conn.execute("PRAGMA table_info(posts)")}
    finally:
        conn.close()


def test_state_migration_adds_verified(tmp_path):
    db = tmp_path / "state.db"
    conn = sqlite3.connect(db)
    conn.execute(
        """
        CREATE TABLE posts (
            sha256 TEXT NOT NULL, account TEXT NOT NULL, platform TEXT NOT NULL,
            backend TEXT, status TEXT NOT NULL, post_id TEXT, post_url TEXT,
            detail TEXT, updated_at REAL NOT NULL,
            PRIMARY KEY (sha256, account, platform)
        )
        """
    )
    conn.commit()
    conn.close()

    StateStore(db)
    assert "verified" in _columns(db)

    StateStore(db)  # idempotent re-open
    assert "verified" in _columns(db)


def test_record_persists_verified(tmp_path):
    db = tmp_path / "state.db"
    store = StateStore(db)
    media = MediaItem(path=tmp_path / "a.mp4", sha256="x", media_type=MediaType.VIDEO)
    store.record(media, "acct", Platform.KWAI, "kwai_mobile",
                 PostStatus.PUBLISHED, verified=True)

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT verified FROM posts").fetchone()
    finally:
        conn.close()
    assert row["verified"] == 1
