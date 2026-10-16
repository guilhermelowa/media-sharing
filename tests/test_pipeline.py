"""Pipeline behaviour: fallback chain, retries, idempotency, rate limits."""

from __future__ import annotations

from media_sharing import pipeline
from media_sharing.backends.base import PublishError
from media_sharing.config import PlatformPolicy, Settings
from media_sharing.models import (
    Account,
    Job,
    MediaItem,
    MediaType,
    Platform,
    PostMetadata,
    PostStatus,
    PublishResult,
)
from media_sharing.state import StateStore


def media(tmp_path):
    p = tmp_path / "clip.mp4"
    p.write_bytes(b"x" * 16)
    return MediaItem(path=p, sha256="abc123", media_type=MediaType.VIDEO, size_bytes=16)


class FakePublisher:
    name = "fake"

    def __init__(self, *, fail=None, result_status=PostStatus.PUBLISHED):
        self._fail = fail
        self._status = result_status

    def prepare(self):
        pass

    def publish(self, m, meta):
        if self._fail:
            raise self._fail
        return PublishResult(platform=Platform.X, backend=self.name, status=self._status,
                             post_id="p1")


def settings_for(chain):
    acct = Account(name="a1", platform=Platform.X)
    return Settings(accounts=[acct], platforms={"x": PlatformPolicy(backends=chain)}), acct


def store(tmp_path):
    return StateStore(tmp_path / "state.db")


def monkey_build(monkeypatch, mapping):
    def fake_build(name, account, settings):
        impl = mapping[name]
        if isinstance(impl, Exception):
            raise impl
        return impl
    monkeypatch.setattr(pipeline.backends, "build", fake_build)
    monkeypatch.setattr(pipeline.time, "sleep", lambda *_: None)


def test_falls_through_unimplemented(monkeypatch, tmp_path):
    s, acct = settings_for(["not_done", "fake"])
    monkey_build(monkeypatch, {"not_done": NotImplementedError(), "fake": FakePublisher()})
    result = pipeline.publish_job(
        Job(media=media(tmp_path), account=acct, metadata=PostMetadata(caption="x")),
        s, store(tmp_path))
    assert result.status is PostStatus.PUBLISHED


def test_falls_through_nonretryable(monkeypatch, tmp_path):
    s, acct = settings_for(["bad", "fake"])
    monkey_build(monkeypatch, {
        "bad": FakePublisher(fail=PublishError("nope", retryable=False)),
        "fake": FakePublisher(),
    })
    result = pipeline.publish_job(
        Job(media=media(tmp_path), account=acct, metadata=PostMetadata()), s, store(tmp_path))
    assert result.status is PostStatus.PUBLISHED


def test_all_backends_fail(monkeypatch, tmp_path):
    s, acct = settings_for(["a", "b"])
    monkey_build(monkeypatch, {
        "a": FakePublisher(fail=PublishError("nope", retryable=False)),
        "b": FakePublisher(fail=PublishError("nope", retryable=False)),
    })
    st = store(tmp_path)
    result = pipeline.publish_job(
        Job(media=media(tmp_path), account=acct, metadata=PostMetadata()), s, st)
    assert result.status is PostStatus.FAILED


def test_retryable_recovers(monkeypatch, tmp_path):
    calls = {"n": 0}

    class Flaky(FakePublisher):
        def publish(self, m, meta):
            calls["n"] += 1
            if calls["n"] == 1:
                raise PublishError("temporary", retryable=True)
            return PublishResult(platform=Platform.X, backend="fake",
                                 status=PostStatus.PUBLISHED)

    s, acct = settings_for(["flaky"])
    monkey_build(monkeypatch, {"flaky": Flaky()})
    result = pipeline.publish_job(
        Job(media=media(tmp_path), account=acct, metadata=PostMetadata()), s, store(tmp_path))
    assert result.status is PostStatus.PUBLISHED
    assert calls["n"] == 2


def test_idempotency(monkeypatch, tmp_path):
    s, acct = settings_for(["fake"])
    monkey_build(monkeypatch, {"fake": FakePublisher()})
    st = store(tmp_path)
    m = media(tmp_path)
    first = pipeline.publish_job(Job(media=m, account=acct, metadata=PostMetadata()), s, st)
    second = pipeline.publish_job(Job(media=m, account=acct, metadata=PostMetadata()), s, st)
    assert first.status is PostStatus.PUBLISHED
    assert second.status is PostStatus.SKIPPED


def test_rate_limit(monkeypatch, tmp_path):
    acct = Account(name="a1", platform=Platform.X)
    s = Settings(accounts=[acct],
                 platforms={"x": PlatformPolicy(backends=["fake"], daily_cap=1)})
    monkey_build(monkeypatch, {"fake": FakePublisher()})
    st = store(tmp_path)
    first = pipeline.publish_job(
        Job(media=media(tmp_path), account=acct, metadata=PostMetadata()), s, st)
    # second distinct file (different sha) hits the daily cap
    m2 = MediaItem(path=tmp_path / "b.mp4", sha256="other", media_type=MediaType.VIDEO)
    m2.path.write_bytes(b"y")
    second = pipeline.publish_job(
        Job(media=m2, account=acct, metadata=PostMetadata()), s, st)
    assert first.status is PostStatus.PUBLISHED
    assert second.status is PostStatus.SKIPPED
    assert second.detail == "rate limit"


def test_failed_attempt_does_not_consume_rate_limit(monkeypatch, tmp_path):
    acct = Account(name="a1", platform=Platform.X)
    s = Settings(accounts=[acct],
                 platforms={"x": PlatformPolicy(
                     backends=["fake"], daily_cap=1, min_interval_seconds=3600)})
    st = store(tmp_path)
    m = media(tmp_path)

    # A failure must not advance the counter / interval.
    monkey_build(monkeypatch, {"fake": FakePublisher(
        fail=PublishError("boom", retryable=False))})
    failed = pipeline.publish_job(
        Job(media=m, account=acct, metadata=PostMetadata()), s, st)
    assert failed.status is PostStatus.FAILED

    # A later successful publish is still allowed immediately.
    monkey_build(monkeypatch, {"fake": FakePublisher()})
    ok = pipeline.publish_job(
        Job(media=m, account=acct, metadata=PostMetadata()), s, st)
    assert ok.status is PostStatus.PUBLISHED

    # Now the daily cap is consumed.
    m2 = MediaItem(path=tmp_path / "b.mp4", sha256="other", media_type=MediaType.VIDEO)
    m2.path.write_bytes(b"y")
    capped = pipeline.publish_job(
        Job(media=m2, account=acct, metadata=PostMetadata()), s, st)
    assert capped.detail == "rate limit"


def test_manual_required_does_not_consume_rate_limit(monkeypatch, tmp_path):
    acct = Account(name="a1", platform=Platform.X)
    s = Settings(accounts=[acct],
                 platforms={"x": PlatformPolicy(
                     backends=["fake"], daily_cap=1, min_interval_seconds=3600)})
    st = store(tmp_path)
    monkey_build(monkeypatch, {"fake": FakePublisher(
        result_status=PostStatus.MANUAL_REQUIRED)})

    first = pipeline.publish_job(
        Job(media=media(tmp_path), account=acct, metadata=PostMetadata()), s, st)
    assert first.status is PostStatus.MANUAL_REQUIRED

    # Unconfirmed posts are not counted, so a second run is not rate-limited.
    m2 = MediaItem(path=tmp_path / "b.mp4", sha256="other", media_type=MediaType.VIDEO)
    m2.path.write_bytes(b"y")
    second = pipeline.publish_job(
        Job(media=m2, account=acct, metadata=PostMetadata()), s, st)
    assert second.status is PostStatus.MANUAL_REQUIRED
