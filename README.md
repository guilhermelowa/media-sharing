# media_sharing

Watch a folder of media files and automatically publish them to multiple social
networks through **pluggable publisher backends**.

Drop a file into `media/inbox/` (optionally under an account subfolder) and it is
hashed, fanned out to every enabled account, and published. Files are moved to
`media/processed/` on success or `media/failed/` otherwise. Every outcome is
recorded in SQLite so re-running never double-posts.

> **Template:** this repo ships without any accounts pre-configured. Every
> account in `config/accounts.yaml` is a placeholder with `enabled: false`, so a
> fresh clone never posts anything until you fill in your own accounts. See
> [Setup](#setup) and [`docs/auth.md`](docs/auth.md). The watcher, pipeline, state
> store, config, CLI and publisher backends are all implemented; see
> [`docs/plan.md`](docs/plan.md).

## Supported platforms

| Platform | Backend | Approach | Auth |
|---|---|---|---|
| X (Twitter) | `x_playwright` | Playwright browser session (no API cost) | `media-sharing login x <account>` |
| TikTok | `tiktok_playwright` | Playwright browser session (no dev app/audit) | `media-sharing login tiktok <account>` |
| TikTok | `tiktok_mobile` | Android app via ADB + Maestro (last fallback) | `media-sharing mobile-setup <account>` |
| Kwai | `kwai_mobile` | Android app via ADB + Maestro (no official API) | `media-sharing mobile-setup <account>` |
| Instagram | `instagram_mobile` | Android app via ADB + Maestro | `media-sharing mobile-setup <account>` |
| Bluesky | `bluesky_api` | Official AT Protocol | handle + app password in `.env` |
| TikTok | `tiktok_api` | Official Content Posting API (optional) | OAuth token (draft works unaudited) |
| X (Twitter) | `x_api` | Official API v2 (optional, pay-per-use) | OAuth 1.0a keys |
| any vendor | `aggregator` | Upload-Post / Ayrshare / Blotato | vendor API key |

See [`docs/auth.md`](docs/auth.md) for step-by-step authentication for each
publisher and [`docs/mobile.md`](docs/mobile.md) for the ADB + Maestro mobile
backends.

## Design in one paragraph

Every platform is served by an **ordered chain of backends**
(`config/platforms.yaml`). The pipeline tries each backend until one succeeds,
handling retries, rate limits (`state/state.db`), and per-platform caption
overrides (`.json` sidecar). Backends are selected by name from a registry, so
swapping "self-built API" for "audited aggregator" is a config change, not a
rewrite. Adding a platform = one new file in `src/media_sharing/backends/` plus a
registry entry.

## Layout

```
config/            accounts.yaml, platforms.yaml
media/inbox/       drop files here (<account>/ subdir routes to one account)
media/processed/   moved here on success
media/failed/      moved here on failure
state/state.db     idempotency ledger + rate-limit counters
src/media_sharing/
  models.py          domain types (MediaItem, PostMetadata, PublishResult, ...)
  config.py          YAML + env loading
  state.py           SQLite store
  watcher.py         inbox filesystem watcher
  pipeline.py        backend selection, fallback, retries, metadata resolution
  media_host.py      transient local URL server (for URL-only platforms)
  backends/          publisher interface + registry + backends
  backends/mobile.py shared ADB + Maestro helpers (Kwai/Instagram/TikTok app)
  sessions/          Playwright session state per account (gitignored)
config/mobile/       Maestro flows per app (user-calibrated)
  cli.py             `media-sharing` command
docs/plan.md         full design and decision log
docs/plans/          deferred work (Instagram)
```

## Setup

This project is managed with [uv](https://docs.astral.sh/uv/).

```bash
uv sync --all-extras            # base + every optional backend + dev tools
uv run playwright install chromium   # browser binaries for *_playwright backends
cp .env.example .env                  # fill in only the credentials you'll use
# edit config/accounts.yaml: add your accounts and set enabled: true
```

Prefer a minimal install? Sync only the extras you need:

```bash
uv sync --extra playwright --dev
```

Then run every command through `uv run` (no manual activation needed):

```bash
uv run media-sharing run
```

### Sharing & secrets

This is a template, so keep your real data out of version control:

- **Never commit `.env`.** It holds API keys/app passwords and is gitignored; use
  `.env.example` as the template. Before sharing a copy, revoke/rotate any
  credentials you have ever put there (e.g. Bluesky app passwords at
  <https://bsky.app/settings/app-passwords>).
- **Never share local runtime state.** These are gitignored but will leak if you
  zip the whole folder: `.env`, `src/media_sharing/sessions/*.json` (logged-in
  browser cookies), `state/*.db` (posting ledger), and `debug/` (UI dumps and
  account screenshots). Archive with `git archive` rather than copying the
  directory.
- Accounts ship with `enabled: false`; nothing posts until you opt in.

## Usage

Run from the project root with `uv run`:

```bash
uv run media-sharing run                       # watch inbox/ and publish as files arrive
uv run media-sharing post ./clip.mp4           # publish one file now
uv run media-sharing login x my_x              # capture a browser session (X, TikTok, Kwai)
uv run media-sharing import-session tiktok my_tiktok --browser firefox  # copy a real-browser session
uv run media-sharing discover my_kwai          # headed browser to find selectors
uv run media-sharing devices                   # list connected ADB devices
uv run media-sharing mobile-setup my_kwai      # check phone/app, open the app (mobile backends)
uv run media-sharing inspect my_kwai           # dump phone UI + screenshot to debug/
uv run media-sharing list-backends             # registered backends (marks login/mobile ones)
uv run media-sharing accounts                  # configured accounts
```

### Sidecar metadata

Alongside `clip.mp4`, add `clip.mp4.json` for per-platform captions:

```json
{
  "default": { "caption": "Launch day!", "hashtags": ["#ai", "#launch"] },
  "overrides": {
    "tiktok": {
      "caption": "watch till the end",
      "hashtags": ["#fyp", "#viral"],
      "options": {
        "location": "Your City",
        "headless": true
      }
    },
    "x": { "caption": "Shipped. https://example.com" }
  }
}
```

`caption` and `hashtags` are merged into the post text. `options` are
backend-specific: for TikTok the Playwright backend reads `location` (picked from
the Studio location search) and `headless`; the API backend reads `privacy_level`,
`mode`, `disable_duet/comment/stitch`, etc.

## Trade-offs (summary)

- **Official APIs** cover Bluesky/X/TikTok but not Kwai. TikTok posts are
  private/draft until the app passes TikTok's audit. X is pay-per-use (~$0.015
  per post, $0.20 if the caption contains a URL).
- **Playwright** is the only option for Kwai (and a fallback for Instagram). It
  needs persisted sessions and carries ToS/ban risk.
- **Aggregators** hold audited apps (useful for public TikTok without an audit)
  but cost monthly and **none support Kwai**.

See [`docs/plan.md`](docs/plan.md) for the full comparison and
[`docs/plans/future_plans.md`](docs/plans/future_plans.md) for deferred work.
