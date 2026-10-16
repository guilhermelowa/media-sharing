# media_sharing — Design & Plan

Watch a directory of media files and automatically publish each one to all
configured social accounts. This document is the decision record and the spec the
implementing agent builds against.

## 1. Goal

Given a media file dropped in `media/inbox/`, publish it to **Kwai, TikTok, X,
Bluesky** (Instagram later) for every enabled account, without manual steps,
without double-posting, and without uploading originals to third parties where
avoidable.

## 2. Platform reality (2026)

| Platform | Official posting API? | Key gate | Self-built route viability |
|---|---|---|---|
| Bluesky | Yes — AT Protocol | None; app password | Straightforward (free) |
| X | Yes — API v2 (pay-per-use) | API costs ~$0.015/post, $0.20/URL. **Default route: `x_playwright` browser session** — no cost | Use Playwright (default); API optional |
| Instagram | Yes — Graph / IG Login | Business/Creator + linked FB Page; images require a **public URL** | Android app via ADB + Maestro (`instagram_mobile`) |
| TikTok | Yes — Content Posting API | **Audit** required for public posts; unaudited = SELF_ONLY, private accounts, ≤5 users/day, ~15 posts/day | Works, but private until audited |
| Kwai | **No public posting API** | API surface is read-only/scraping; China-only merchant API | Android app via ADB + Maestro (`kwai_mobile`) |

**Aggregators** (Ayrshare 14 networks, Upload-Post 22, Blotato 9, Postiz 30+,
Post for Me 9) wrap the platforms behind one REST call and hold the audited
developer apps. Billing is per connected profile (~$24–149/mo). **None of them
support Kwai**, so Kwai is automation regardless.

## 3. Architecture

```
inbox/<account>/file.mp4
        │
   watcher.py  (sha256, classify, account from subdir)
        │
   pipeline.fan_out()
        │   for each enabled account:
        │     resolve_metadata(sidecar)  ── default + per-platform override
        │     rate-limit check (state.db)
        │     idempotency check (sha256, account, platform)
        │     for backend in chain[platform]:
        │         backend.publish(media, meta) ── PublishResult | PublishError
        │         retryable errors -> backoff retries; then next backend
        │
   state.record(...)  +  move file -> processed/ | failed/
```

### Core abstractions

- `models.MediaItem` — the file (path, sha256, media type).
- `models.PostMetadata` — resolved caption/hashtags/options for one account.
- `models.Account` / `PublishResult` / `Job` / `PostStatus`.
- `backends.base.Publisher` — `publish(media, meta) -> PublishResult`.
  Raises `PublishError(retryable=..., code=...)`.
- `backends.registry` — name -> class; config references names.
- `config.platforms.yaml` — the ordered backend chain + rate limits per platform.
- `state.StateStore` — idempotency ledger `(sha256, account, platform)` + daily
  counters.

### Backend families

- **`*_api`** — official HTTP APIs.
- **`*_playwright`** — browser automation; persisted `storage_state` per account.
- **`*_mobile`** — drives the official Android app on a shared USB phone via
  `adb` + Maestro; no session file (the app stays logged in). Shared helper:
  `backends/mobile.py`. Used where no web upload flow exists (Kwai, Instagram)
  and as a TikTok fallback.
- **`aggregator`** — one class parameterized by vendor; sends to their unified
  endpoint. Used where an audited app is needed (public TikTok) and as an
  optional fallback.

### Why a chain, not a flag

Fallback ordering lets a single deployment prefer the cheapest/compliant path and
degrade gracefully. Examples:
- `tiktok: [tiktok_api, aggregator]` — try self-built; if it returns
  `UNAUDITED_PRIVATE_ONLY`, fall through to an audited aggregator for a public
  post.
- `kwai: [kwai_playwright]` — no alternative exists.

## 4. Data & idempotency

- Idempotency key `(sha256, account, platform)` ensures a re-run or a re-dropped
  file is never published twice.
- `PublishResult.status` values: `published`, `failed`, `skipped`
  (already-done / rate-limited / disabled), `manual_required` (e.g. TikTok draft
  awaiting the operator, or Kwai captcha).
- `manual_required` counts as "handled" for file moving but is surfaced for
  follow-up.

## 5. Rate limits & safety

- Per-platform `daily_cap` and `min_interval_seconds` enforced in `StateStore`.
- Retries with exponential backoff on `retryable=True` (max 3).
- Kwai/Instagram automation: low volume, dedicated browser context, human-like
  delays, persisted sessions; accept ToS/ban risk.

## 6. Media hosting

Most backends accept raw bytes, so **no hosting is required** for Bluesky, X,
TikTok (`FILE_UPLOAD`), Kwai (browser file input), or Upload-Post (multipart).
Only URL-only flows need a public URL (Instagram images / TikTok
`PULL_FROM_URL`). `media_host.py` serves the local file on a random token path;
front it with a tunnel and set `MEDIA_PUBLIC_BASE_URL` when needed.

## 7. CLI

- `run` — watch inbox continuously.
- `post <file>` — publish one file now.
- `list-backends`, `accounts` — introspection.
- `login <platform> <account>` — capture a browser session.
- `devices`, `mobile-setup <account>`, `inspect <account>` — ADB + Maestro mobile
  workflows (list devices; verify tooling/device/app; dump UI + screenshot).

## 8. Interfaces the implementing agent must satisfy

1. Keep `Publisher.publish` signature unchanged; return `PublishResult`, raise
   `PublishError` with correct `retryable`.
2. Read credentials only from `account.credentials` (already env-expanded) or the
   session dir — never hardcode.
3. Advertise accurate `Capabilities` (media types, `needs_public_url`,
   `stores_session`).
4. For URL-requiring media, request a URL via `media_host.TransientMediaHost`
   rather than inventing one.
5. Signal "posted but needs a human" with `PostStatus.MANUAL_REQUIRED`.

## 9. Milestones

- [x] Scaffold: models, config, state, watcher, pipeline, registry, CLI, docs.
- [x] `bluesky_api` — official AT Protocol (atproto SDK; image + video blobs).
- [x] `x_api` — OAuth 1.0a + v1.1 media upload + v2 post creation (optional).
- [x] `x_playwright` — **default X route**; browser session, no API cost.
- [x] `tiktok_api` — Content Posting API; draft (default) and direct modes (optional).
- [x] `tiktok_playwright` — **default TikTok route**; browser session, no dev app/audit.
- [x] `kwai_playwright` — session-based browser automation.
- [x] `aggregator` — Upload-Post (default) and Ayrshare vendors.
- [x] Generic `media-sharing login <platform> <account>` for browser-session
      accounts; shared `backends/browser.py` used by X, TikTok and Kwai.
- [x] `media-sharing discover <account>` — headed browser that dumps candidate
      selectors (file inputs, caption editors, buttons) to help calibrate.
- [x] Tests: pipeline fallback/idempotency/rate-limit + per-backend request
      construction (mocked). 20 tests, ruff clean.
- [x] `docs/auth.md` — per-publisher authentication guide.
- [x] `kwai_mobile`, `instagram_mobile`, `tiktok_mobile` — ADB + Maestro mobile
      backends; shared `backends/mobile.py`; `config/mobile/*.yaml` flows;
      `devices`/`mobile-setup`/`inspect` CLI; `docs/mobile.md`.
- [ ] Instagram API backend — deferred; see `plans/future_plans.md`.
- [ ] Browser verification of a posted item — deferred (scaffolded `verified`).

### Implementation notes / known limitations

- **Bluesky** uses `client.send_image` / `client.send_video` from the `atproto`
  SDK (lazy-imported). Handle + app password only.
- **X (default, `x_playwright`)** drives the web composer and captures the post
  id from the `CreateTweet` response `rest_id`. No developer app, no per-post
  cost. Falls back to `x_api` only if you change the chain.
- **X (`x_api`, optional)** uses the v1.1 chunked media upload endpoint plus
  `POST /2/tweets`. Pay-per-use (~$0.015/post, $0.20/URL).
- **TikTok (default, `tiktok_playwright`)** drives the TikTok Studio uploader.
  No developer app, ToS/Privacy URLs, or audit. **Video only** for now.
- **TikTok (`tiktok_api`, optional)** supports video only (photo posts use a
  different endpoint). Draft mode returns `MANUAL_REQUIRED`; direct mode needs an
  audited app or the aggregator fallback.
- **Browser backends (X, TikTok, Kwai)** share `backends/browser.py`. Selectors/
  URLs are configurable via `account.options`; defaults need calibration per
  locale (use `media-sharing discover`). No session -> `prepare()` raises
  `session_missing` and the pipeline falls through.
- **Mobile backends (Kwai, Instagram, TikTok)** drive the Android app on a
  shared USB phone via `backends/mobile.py` + per-app Maestro flows in
  `config/mobile/`. No session file; runs are serialised with a device lock
  (`state/mobile.lock`). Tooling (`adb`, `maestro`) must be on `PATH` or
  `prepare()` raises `missing_dependency`. See `docs/mobile.md`.
- **Kwai** is app-centric, so `kwai_mobile` is the backend (the web surface,
  Kwai Studio, is live-streaming only for most accounts). The legacy
  `kwai_playwright` module is kept but is out of the default chain.
- **Aggregator** never supports Kwai; do not list it as a Kwai fallback.

## 10. Open questions

- Which aggregator vendor (if any) to wire first? Upload-Post is the cheapest
  with direct file upload and a Python SDK.
- Kwai region/UI variant to target (kwai.com locale affects selectors).
