# Test plan — Kwai on the shared phone

Manual verification plan for the `kwai_mobile` backend (ADB + Maestro) on the
single, shared, USB-connected Android phone. Kwai is **mobile only**
(`kwai: [kwai_mobile]`); the legacy `kwai_playwright` backend is out of the
default chain.

Background and full setup: [`docs/mobile.md`](../mobile.md) and
[`mobile_backends.md`](mobile_backends.md).

## Preconditions

- [ ] Account `my_kwai` exists and is enabled in
      `config/accounts.yaml`, with `options.app_id: com.kwai.video`.
- [ ] `config/platforms.yaml` has `kwai: [kwai_mobile]`.
- [ ] A test media file (`./clip.mp4`) and, optionally, a sidecar
      `./clip.mp4.json` with a `kwai` caption override.
- [ ] The phone is the only one connected, so no `options.device` is needed.

## 1. Install and connect the phone

- [ ] `adb` (Android platform-tools) and `maestro` are on `PATH`:
      `adb version` and `maestro --version`.
- [ ] USB debugging is enabled and the RSA prompt accepted;
      `adb devices` shows the phone as `device` (not `unauthorized`).
- [ ] On Oppo/Realme/Xiaomi, permission monitoring is disabled so Maestro's
      companion APK installs.

## 2. Verify tooling, device and app; confirm login

```bash
uv run media-sharing devices                          # shows the phone as (selected)
uv run media-sharing mobile-setup my_kwai # opens the Kwai app
```

- [ ] `devices` lists exactly one device, marked `(selected)`.
- [ ] `mobile-setup` reports the device, `com.kwai.video (installed)`, opens the
      app, and lets you confirm you are logged in.
- [ ] Nothing is persisted (`mobile-setup` is the mobile analog of `login`).

Failure codes and fixes: `missing_dependency` (adb/maestro not on `PATH`),
`no_device` / `multiple_devices` (connect one phone or set `options.device`),
`app_missing` (wrong package / not installed).

## 3. Calibrate the flow (required)

The shipped `config/mobile/kwai.yaml` uses PLACEHOLDER selectors; posting will
fail (`flow_failed`, retryable) until it is calibrated.

```bash
uv run media-sharing inspect my_kwai      # dumps debug/<acct>-ui.xml + .png
maestro studio                                        # live UI tree of the device
```

- [ ] `inspect` writes `debug/my_kwai-ui.xml` and `.png`.
- [ ] Replace each `# TODO` selector in `config/mobile/kwai.yaml`
      (create/plus → Upload → Files → `${MEDIA}` → Next → caption → Post →
      assertion) with the real label from `maestro studio`.
- [ ] The flow reads the variables `CAPTION`, `MEDIA` (filename) and
      `MEDIA_PATH` (full on-device path).

## 4. Dry-run the Maestro flow alone

```bash
adb shell mkdir -p /sdcard/Download/media_sharing
adb push ./clip.mp4 /sdcard/Download/media_sharing/
maestro test config/mobile/kwai.yaml \
  -e CAPTION="hello #test" -e MEDIA=clip.mp4 \
  -e MEDIA_PATH=/sdcard/Download/media_sharing/clip.mp4
```

- [ ] Maestro exits 0 and the post appears in the Kwai app.
- [ ] The final `assertVisible` (confirmation) passes.
- [ ] The file appears via the SAF "Files/Downloads" picker (primary path).

## 5. Full pipeline post

```bash
uv run media-sharing post ./clip.mp4 --account my_kwai
```

- [ ] The command reports `published` for the `kwai_mobile` backend.
- [ ] The post is visible in the app.
- [ ] `debug/mobile/my_kwai/<sha256>/maestro.xml` and the Maestro
      test output were written.
- [ ] Re-running the same file is a no-op (`skipped`, idempotency).
- [ ] Rate limits hold: `daily_cap: 5`, `min_interval_seconds: 60`.

## 6. Failure handling

- [ ] A held `state/mobile.lock` (e.g. a concurrent `post`) surfaces as
      `device_busy` and is treated as retryable.
- [ ] A broken flow (bad selector) surfaces as `flow_failed` (retryable) and the
      pipeline backs off/retries, then fails cleanly.
- [ ] Optional: set `options.expected_handle` and verify a mismatched active
      account fails `prepare()` with `account_mismatch` (best-effort guard).

## Known limitations

- No `post_url`: success relies on the in-app Maestro assertion; browser
  verification is deferred (scaffolded `verified`).
- UI automation is fragile across app updates/locales — re-run `inspect` and
  update the YAML, and keep volume low (ToS/ban risk).
