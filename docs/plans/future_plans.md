# Future Plans

Deferred work, kept out of the initial scaffold. Each item is self-contained so
it can be picked up independently.

---

## Instagram publishing

**Status:** the **mobile backend (`instagram_mobile`) is implemented** — it
drives the official Android app via ADB + Maestro, avoiding the public-URL
requirement. See [`mobile_backends.md`](mobile_backends.md) and `docs/mobile.md`.
The API/Playwright backends below remain **deferred**.
**Why the API is deferred:** Instagram's Graph API requires a *public URL* for
image media (Meta fetches it), plus a Business/Creator account linked to a
Facebook Page and app review for production. It adds infrastructure the other
platforms don't need.

### Backends still to implement

Two selectable API/browser backends, same interface as the rest:

1. **`instagram_api`** — official Instagram Graph API / Instagram API with
   Instagram Login.
2. **`instagram_playwright`** — drive the web uploader's file input (like Kwai),
   avoiding the public-URL requirement entirely.

Register both in `src/media_sharing/backends/registry.py` (uncomment the lines
already present there) and add chains to `config/platforms.yaml`:

```yaml
instagram:
  backends: [instagram_api, instagram_playwright]
  daily_cap: 50
  min_interval_seconds: 30
```

### `instagram_api` — implementation notes

- **Prerequisites**
  - Instagram **Business or Creator** account.
  - Linked **Facebook Page** (required even if you never post to Facebook).
  - A Meta developer app; permissions `instagram_basic` +
    `instagram_content_publish` (and `instagram_manage_comments` for first
    comment). Production beyond ~25 test users needs App Review.
  - Two auth paths: *Instagram API with Instagram Login*
    (`graph.instagram.com`, perms `instagram_business_*`) or *Facebook Login*
    (`graph.facebook.com`, Page token). Prefer Instagram Login for publishing
    only.
- **Publishing flow** (container → publish):
  1. `POST /{ig-user-id}/media` with `image_url` | `video_url` (+ `caption`,
     `media_type`, `is_carousel_item`, etc.) → returns `creation_id`.
     - **Images require a public URL.** Video can use resumable upload via
       `rupload.facebook.com` (Facebook Login path) to avoid hosting.
  2. Poll `GET /{creation_id}?fields=status_code` until `FINISHED`.
  3. `POST /{ig-user-id}/media_publish?creation_id=...`.
  4. (Optional) first comment via `/{media-id}/comments`.
- **Limits:** 50–100 API posts / 24h; carousel = 1 post; caption ≤ 2,200 chars,
  ≤ 30 hashtags, ≤ 20 @mentions; JPEG only for images; carousels 2–10 items with
  a shared aspect ratio.
- **`Capabilities(needs_public_url=True)`** for the image path.
- **Public URL sourcing:** use `media_host.TransientMediaHost`
  (`src/media_sharing/media_host.py`), which serves the file locally under an
  unguessable token path. Expose it with a tunnel (cloudflared/ngrok) and set
  `MEDIA_PUBLIC_BASE_URL`; Meta fetches during the few seconds of the call, then
  the server is stopped. Nothing is persisted to a third party.
- **Rate limiting:** build the container count into `StateStore` checks.

### `instagram_playwright` — implementation notes

- **Goal:** publish without any public URL by selecting the local file in the
  web uploader, mirroring `kwai_playwright.py`.
- Persisted session at `settings.paths.sessions / f"{account.name}.json"`.
- Provide `instagram-login <account>` analogous to `kwai-login`.
- Fragile against UI changes; accept ToS/ban risk; low volume.

### Open questions for Instagram

- Instagram Login vs Facebook Login path (publishing-only suggests IG Login).
- Reels/Stories support in scope, or feed images/videos only?
- Carousel support now, or single-item first?

### References

- Content Publishing: https://developers.facebook.com/documentation/instagram-platform/content-publishing
- IG User media_publish: https://developers.facebook.com/documentation/instagram-platform/instagram-graph-api/reference/ig-user/media_publish
- IG media (container): `/{ig-user-id}/media`
- Permissions reference: `instagram_business_basic`,
  `instagram_business_content_publish`, `instagram_basic`,
  `instagram_content_publish`, `pages_read_engagement`

---

## Other deferred items

### Browser verification of a posted item
Mobile (and weak-signal) backends only assert success *in-app*, so they cannot
produce a `post_url` and may report success when a post silently failed. Add an
optional verification step that uses Playwright against the platform's **web
profile** to confirm the item appears, then sets `PublishResult.verified`
(scaffolded now — nullable `verified` column in `state.py`, no writer yet).
Suggested shape: non-fatal by default, gated by `options.verify` and a
`--verify` CLI flag.

### TikTok public Direct Post (audit)
Self-built apps are limited to private/draft posts until they pass TikTok's
compliance audit. Until then: use `options.mode: draft` (posts to the inbox for
in-app publishing) or route through the `aggregator` backend (audited vendor
app). Track the audit checklist before enabling public Direct Post.

### Aggregator vendor selection
Decide which vendor to implement first in `backends/aggregator.py`. Upload-Post
is the cheapest with direct file upload (`-F video=@/path`) and a Python SDK;
Ayrshare is broader for analytics/comments. None support Kwai.

### Media transcoding
Platforms have format/size/aspect constraints (TikTok MP4/H.264, IG JPEG-only
images, Reels 9:16, etc.). Add an optional FFmpeg-based preprocessing step
(`pillow` for images) gated per backend capability before upload.

### Scheduling
`PostMetadata.scheduled_at` exists but is unused. Wire native platform scheduling
where supported, otherwise a local scheduler that delays enqueue.

### Secrets management
`.env` is fine for local use. For deployment, move credentials to a secrets
manager and reference them via the existing `env:` indirection in
`accounts.yaml`.

### Observability
Add structured logging, a `status` CLI command backed by `state.db`, and optional
webhook/notification on `failed` or `manual_required` outcomes.
