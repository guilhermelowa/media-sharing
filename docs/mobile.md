# Mobile backends (ADB + Maestro)

Some platforms have no usable web upload flow (Kwai's Studio is live-streaming
only; Instagram's API needs a public URL + Business account + app review). For
those, `media_sharing` can drive the **official Android app** on a single,
shared, USB-connected phone:

| Backend | App package (verify on device) | Platform | Role in chain |
|---|---|---|---|
| `kwai_mobile` | `com.kwai.video` | Kwai | **only** backend |
| `instagram_mobile` | `com.instagram.android` | Instagram | **only** backend |
| `tiktok_mobile` | `com.zhiliaoapp.musically` (region variants exist) | TikTok | **last** fallback |

The shared helper is `src/media_sharing/backends/mobile.py`; each platform is a
thin module that sets `DEFAULTS`, `capabilities` and a `name`. There is **no new
Python dependency** — everything goes through `adb` and `maestro` on `PATH`.

Unlike browser backends, **no login/session file is created**: the app stays
logged in on the phone. All runs on the phone are serialised with a device lock
(`state/mobile.lock`), so a watcher run and a manual `post` never drive the phone
at the same time.

---

## 1. Install the tooling

- **`adb`** — Android platform-tools. Verify with `adb version`.
- **Maestro** — the UI-flow runner: <https://maestro.mobile.dev>. Verify with
  `maestro --version`. It installs a companion APK on the device.

Both must be on `PATH`. `media-sharing devices` / `mobile-setup` check this and
raise `missing_dependency` with install hints if they are absent.

## 2. Prepare the phone

1. Enable **Developer options** → **USB debugging**.
2. Connect over USB and accept the RSA prompt; `adb devices` should show it as
   `device` (not `unauthorized`).
3. **Disable permission monitoring** ("Verify apps over USB" / MIUI "USB
   debugging (Security settings)") on Oppo/Realme/Xiaomi so the Maestro companion
   APK installs. MDM-locked/hardened devices may refuse it — use a standard,
   unlocked phone.
4. Log in to each app once, manually. Accounts can be switched between runs (the
   app is the source of truth).

## 3. Configure accounts

```yaml
  - name: my_instagram
    platform: instagram
    enabled: true
    options:
      app_id: com.instagram.android
      # flow: config/mobile/instagram.yaml   # default for this backend
      # device: <adb-serial>                 # only if not using the shared phone
      # media_dir: /sdcard/Download/media_sharing
      # expected_handle: my_handle           # best-effort guard (see §6)
    credentials: {}
```

The fallback chain lives in `config/platforms.yaml`
(`kwai: [kwai_mobile]`, `instagram: [instagram_mobile]`,
`tiktok: [tiktok_playwright, tiktok_api, tiktok_mobile]`).

## 4. Commands

```bash
media-sharing devices            # list adb devices + which is selected
media-sharing mobile-setup my_instagram   # check adb/maestro/device/app, open the app
media-sharing inspect my_instagram        # dump UI xml + screenshot to debug/
media-sharing post ./clip.mp4 --account my_instagram
```

- `devices` needs only `adb`. With more than one device connected, set
  `options.device` on the account.
- `mobile-setup` is the mobile analog of `login`: it verifies the tooling, the
  device, and that the app is installed, then opens the app so you can confirm
  you are logged in. **It persists nothing.**
- `inspect` is the analog of `discover`: it dumps
  `adb exec-out uiautomator dump` and a screenshot to `debug/`, then points you
  at `maestro studio`.

## 5. Calibrating and authoring flows

Each app needs one commit-able, user-calibrated flow at
`config/mobile/<platform>.yaml` (see the shipped PLACEHOLDER flows). Shape:

```yaml
appId: com.kwai.video
---
- launchApp:
    clearState: false
- tapOn: "…"            # create / plus
- tapOn: "…"            # Upload / Album / Files
- tapOn: "${MEDIA}"     # staged media, by filename
- tapOn: "Next"
- tapOn: "…"            # caption field
- inputText: "${CAPTION}"
- tapOn: "…"            # Share / Publish
- assertVisible: "…"    # confirmation
```

Variables passed by the backend: `CAPTION`, `MEDIA` (staged filename) and
`MEDIA_PATH` (full on-device path).

To find the real selectors:

```bash
media-sharing inspect my_kwai     # opens nothing; dumps the current screen
maestro studio                   # live UI tree of the connected device
```

Then edit `config/mobile/<platform>.yaml`. Flow failures are treated as
**retryable** (UI automation is flaky), so the pipeline backs off and retries
before falling through to the next backend. The Maestro JUnit report and test
output are saved under `debug/mobile/<account>/<sha256>/`.

## 6. Media-indexing strategy

The backend pushes the file to `/sdcard/Download/media_sharing/` and drives the
app's **SAF "Files/Downloads"** picker (primary). On Android 10+ the old
`MEDIA_SCANNER_SCAN_FILE` broadcast is blocked, so a pushed file may not appear
in the gallery; the backend still attempts a best-effort MediaStore rescan as a
fallback for older devices. Prefer the Files/Downloads entry in your flows.

## 7. Shared device & account switching

- One phone is shared by all mobile accounts; runs are serialised by the device
  lock and are inherently low-volume (see the `platforms.yaml` caps).
- Because the app's login is global, a run can post to whichever account is
  currently active. Set `options.expected_handle` for a **best-effort** guard:
  `prepare()` probes `dumpsys account` and fails with `account_mismatch` only
  when it can positively tell the wrong account is active.
- Until browser verification is built (deferred), success relies on the in-app
  Maestro assertion — there is no `post_url`.

## 8. Smoke checklist

```bash
adb start-server && adb devices                       # one device in "device" state
media-sharing devices                                 # shows it as (selected)
media-sharing mobile-setup <account>                  # opens the app; confirm login
media-sharing inspect <account>                       # writes debug/<account>.png + -ui.xml
# author config/mobile/<platform>.yaml with `maestro studio`, then dry-run:
maestro test config/mobile/<platform>.yaml -e CAPTION=hello -e MEDIA=clip.mp4
media-sharing post ./clip.mp4 --account <account>
```

## 9. Troubleshooting

- **`missing_dependency`** — `adb`/`maestro` not on `PATH`.
- **`no_device` / `multiple_devices`** — connect exactly one phone, or set
  `options.device`.
- **`app_missing`** — install the app (package names above; some regions use a
  different TikTok package).
- **`device_busy`** — another run holds `state/mobile.lock`; wait for it.
- **Companion APK blocked** — relaxed the security setting in §2.
- **Flow breaks after an app update** — re-run `inspect` + `maestro studio` and
  update the YAML; keep selectors textual and volume low (ToS/ban risk).
