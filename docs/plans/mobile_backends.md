# Mobile backends (ADB + Maestro)

**Status:** implemented (see `docs/mobile.md`). Browser verification remains
deferred (scaffolded `verified`).
**Why:** Kwai has no usable web upload flow (Kwai Studio is live-streaming only,
gated behind live permission), and Instagram's API path requires a public URL +
Business account + app review. Driving the **real Android app** on a phone
(through ADB + Maestro) is the lightest viable route for both, and an optional
fallback for TikTok.

This document is the implementation plan. Each section is self-contained so work
can be picked up independently.

---

## 1. Goal

Add three mobile publisher backends that drive the official Android apps on a
single **shared, USB-connected phone**:

| Backend | App package (verify on device) | Platform | Role in chain |
|---|---|---|---|
| `kwai_mobile` | `com.kwai.video` | Kwai | **only** backend |
| `instagram_mobile` | `com.instagram.android` | Instagram | **only** backend (first IG backend) |
| `tiktok_mobile` | `com.zhiliaoapp.musically` (region variants exist) | TikTok | **last fallback** after `tiktok_playwright`, `tiktok_api` |

This mirrors the existing backend design: a shared helper module (like
`backends/browser.py`) plus thin per-platform modules (like
`backends/x_playwright.py`) registered in `backends/registry.py`. The
`Publisher` contract and the pipeline (fallback chain, retries, idempotency,
rate limits) are unchanged.

## 2. Locked decisions

- **Shared phone**, **Android only**, one adb target reused by all mobile
  accounts. Runs are serialized with a device lock.
- **Three backends retained by name** (consistent with existing naming).
- **Chains:** Kwai → mobile only; Instagram → mobile only; TikTok →
  `tiktok_playwright` first, `tiktok_mobile` appended last.
- **Media picker:** SAF "Files/Downloads" primary, gallery fallback.
- **Post confirmation:** in-app Maestro assertion only. **Browser verification is
  deferred** (see `docs/plans/future_plans.md`), but the `verified` field/column is
  added now as forward-compatible scaffolding (Option A).

## 3. Architecture

### 3.1 Shared helper — `src/media_sharing/backends/mobile.py`

Mirrors `browser.py`. Uses `subprocess` only; **no new Python dependencies**.

- `MaestroConfig` dataclass:
  `app_id`, `flow` (path), `device` (adb serial, `None` = auto),
  `media_dir` (device staging, default `/sdcard/Download/media_sharing`),
  `caption_max_len`, `step_timeout_ms`, `extra`.
- `cfg(account, DEFAULTS)` — merge `account.options` over defaults (same logic as
  `browser.cfg`).
- `require_tools()` — check `shutil.which("maestro")` and `shutil.which("adb")`;
  raise `PublishError(code="missing_dependency")` with install hints.
- `list_devices()` / `resolve_device(cfg)` — parse `adb devices`; error if 0, or
  if >1 and no `device` configured.
- `app_installed(pkg, device)` — `adb -s <serial> shell pm list packages`.
- `push_media(media, device, dest_dir)` — `adb shell mkdir -p` then `adb push`;
  returns the on-device path. Prefer SAF-visible `Download/` over gallery.
- `index_media(on_device_path, device)` — best-effort MediaStore rescan for the
  gallery fallback (see Risks §8.1).
- `run_flow(cfg, device, env, artifacts)` — `maestro --device <serial> test
  <flow> -e KEY=VALUE ... --format junit --output <xml>`; map exit code + JUnit to
  success/failure; store screenshots/report under
  `debug/mobile/<account>/<sha256>/`.
- `device_lock(settings)` — `fcntl.flock` on `state/mobile.lock`, held across
  push → flow, so a watcher run and a manual `post` cannot drive the phone
  simultaneously.

### 3.2 Per-platform modules

Each is a thin subclass (~40 lines), like `x_playwright.py`:

- `kwai_mobile.py` — `KwaiMobilePublisher` (`name = "kwai_mobile"`).
- `tiktok_mobile.py` — `TikTokMobilePublisher`.
- `instagram_mobile.py` — `InstagramMobilePublisher`.

Each provides `DEFAULTS`, `capabilities = Capabilities(platforms=(...),
media_types=("video", "image"), needs_public_url=False, stores_session=False)`,
and:

- `prepare()` — validate adb + device + app installed; optionally assert the
  logged-in handle matches `options.expected_handle` (best-effort).
- `healthcheck()` — device reachable and app installed.
- `publish(media, meta)` — build config, acquire device lock, push media, run the
  platform flow with caption env vars, return `PublishResult`.

**No login/session file** is created: the app stays logged in on the phone
(users may switch accounts between runs — see Risks §8.4). `LOGIN_BACKENDS`
(browser-only) and the `login` command are unchanged.

### 3.3 Maestro flows — `config/mobile/<platform>.yaml`

One commit-able, user-calibrated flow per app. Shape:

```yaml
appId: com.kwai.video
---
- launchApp:
    clearState: false
- tapOn: "…"            # create / plus
- tapOn: "…"            # Upload / Album / Files
- tapOn: "…"            # staged media (most-recent, or by filename)
- tapOn: "Next"
- tapOn: "…"            # caption field
- inputText: ${CAPTION}
- tapOn: "…"            # Share / Publish
- assertVisible: "…"    # confirmation
```

Variables passed via `-e CAPTION=…` (and `-e MEDIA=…` for the staged filename).
Per-account overrides via `account.options.flow` and `account.options.app_id`.

## 4. CLI additions (`src/media_sharing/cli.py`)

- `media-sharing devices` — list adb devices and which one is selected.
- `media-sharing mobile-setup <account>` — verify adb / Maestro / device / app
  installed; open the app so the user confirms they are logged in. The mobile
  analog of `login` (persists nothing).
- `media-sharing inspect <account>` — dump `adb exec-out uiautomator dump` +
  screenshot to `debug/`, and print the "run `maestro studio`" hint. The analog
  of `discover`.

Routing via a new `MOBILE_BACKENDS` map in `registry.py` (kept separate from
`LOGIN_BACKENDS`).

## 5. Config changes

`config/platforms.yaml`:

```yaml
kwai:
  backends: [kwai_mobile]
tiktok:
  backends: [tiktok_playwright, tiktok_api, tiktok_mobile]
instagram:
  backends: [instagram_mobile]
  daily_cap: 25
  min_interval_seconds: 30
```

`config/accounts.yaml` — add an Instagram account (enabled) and mobile options:

```yaml
  - name: my_instagram
    platform: instagram
    enabled: true
    options:
      app_id: com.instagram.android
      # flow: config/mobile/instagram.yaml
      # device: <adb-serial>        # only if not using the shared phone
      # media_dir: /sdcard/Download/media_sharing
      # expected_handle: my_handle
    credentials: {}
```

`config.py` — `Paths`:

- `mobile_flows = REPO_ROOT / "config" / "mobile"`
- `artifacts = REPO_ROOT / "debug" / "mobile"`

## 6. Model & state changes (Option A)

Forward-compatible scaffolding for deferred browser verification.

- `models.py` — `PublishResult.verified: bool | None = None`.
- `state.py` — add a nullable `verified` column and an idempotent migration
  (existing DBs are created with `CREATE TABLE IF NOT EXISTS`, so the new column
  must be added explicitly):

  ```python
  cols = {r["name"] for r in conn.execute("PRAGMA table_info(posts)")}
  if "verified" not in cols:
      conn.execute("ALTER TABLE posts ADD COLUMN verified INTEGER")
  ```

  Extend `record()` to accept `verified: bool | None = None` and persist it
  (`INTEGER`, `None` for now). No writer until verification is implemented.

## 7. Testing

`tests/test_mobile.py`, all with mocked `subprocess.run` (no device required):

- command construction: `adb push`, mkdir, optional scan, `maestro --device …
  test … -e KEY=VALUE`, `--format junit --output`;
- `require_tools()` dependency errors (missing adb / maestro);
- device resolution (0 / 1 / many devices);
- JUnit + exit code → `PublishResult` mapping;
- device lock (second acquisition blocks/fails while held);
- `state.py` migration adds `verified` to an existing DB and is idempotent.

Manual smoke checklist (in `docs/mobile.md`):
`devices` → `mobile-setup` → `inspect` → dry-run flow → `post ./clip.mp4
--account …`.

## 8. Risks

1. **Pushed file reaching the app picker.** On Android 10+ the
   `MEDIA_SCANNER_SCAN_FILE` broadcast is blocked, so a pushed file may not appear
   in the gallery. Mitigations, in order: (a) drive the app's **SAF
   "Files/Downloads"** picker reading a plain directory (primary — no indexing
   needed); (b) push to `/sdcard/Movies/` + best-effort MediaStore
   `content insert`/rescan (fallback); (c) pre-sync via a file manager for
   testing. **Must be validated on the actual phone/API level.**
2. **Maestro companion APK** must be installable. MDM-locked/hardened phones
   (some Oppo/Realme/Xiaomi) block it — use a standard unlocked device.
3. **UI fragility / A-B tests / locale.** Flows break on app updates; keep
   selectors text/regex and volume low.
4. **One shared phone** = serialized posting and shared login state across
   accounts; account switching between runs may leave the wrong account active
   (`expected_handle` is a best-effort guard).
5. **Weak confirmation.** Until browser verification is built, success relies on
   the in-app assertion; there is no `post_url`.
6. **ToS/ban risk** is higher than browser automation; the `platforms.yaml` caps
   still apply.
7. **External tooling** (`adb`, `maestro`) must be installed and on `PATH`;
   documented in `docs/mobile.md`.

## 9. Documentation

- New `docs/mobile.md`: install `adb` + Maestro, device prep (USB debugging,
  disable permission monitoring, allow companion APK), package names, calibration
  with `maestro studio`, flow authoring, media-indexing strategy, shared-device
  and account-switching notes.
- Update `README.md` (platform table), `docs/auth.md`, `docs/plan.md`.
- `docs/plans/future_plans.md`: mark Instagram mobile as planned here; add the
  deferred **browser verification** item (uses Playwright against the platform's
  web profile, non-fatal by default, `options.verify` / `verify` CLI).
- `.gitignore`: ignore `debug/mobile/`.

## 10. Out of scope (now)

- **Browser verification** of a posted item — deferred (field/column scaffolded
  in §6).
- iOS (Maestro supports it, but requires macOS + XCUITest).
- Emulator support / Appium.
- Multi-device parallelism.
- Media transcoding (separate deferred item).
