# Authentication Guide

How each publisher authenticates, in the order you'll do it when setting up a new
account. Two families:

- **Browser session** (`x_playwright`, `tiktok_playwright`, `kwai_playwright`) —
  no developer app, no API keys, no per-post cost. You log in once in a real
  browser; the session is saved and reused. **Start here.**
- **Mobile app** (`kwai_mobile`, `instagram_mobile`, `tiktok_mobile`) — drives
  the real Android app on a shared USB phone via ADB + Maestro. Used where no web
  upload flow exists. No session file: the app stays logged in. See
  [`docs/mobile.md`](mobile.md).
- **Official API** (`bluesky_api`, `tiktok_api`, optional `x_api`) — needs a
  developer account and tokens. Free for Bluesky/TikTok; X's API is pay-per-use.

> Accounts don't exist yet? Create them on each platform first, then follow the
> matching section below. Credentials live in `.env` (never in YAML); sessions
> live in `src/media_sharing/sessions/<account>.json` (gitignored).

---

## 1. Browser-session accounts (recommended)

Flow for X and TikTok (Kwai is app-only now — see §6):

1. Add the account to `config/accounts.yaml` with `enabled: true`.
2. Run the login command. A real browser window opens.
3. Sign in (and complete 2FA / captcha if prompted).
4. The session is written to `sessions/<account>.json` and reused for posting.

```bash
media-sharing login x my_x
media-sharing login tiktok my_tiktok
```

Tips:
- Sessions expire periodically — just re-run `login` when posts start failing
  with `code=session_missing` or a redirect to the login page.
- Run with `headless: false` in the account's `options` while first testing.
- The browser needs `playwright install chromium` (installed via the
  `media-sharing[playwright]` extra).

### Importing a session from a real browser (when login is blocked)

Platforms heavily bot-detect the *login* form, so `login` can fail with
"Maximum number of attempts reached" or endless captchas. You can skip the login
flow entirely: sign in manually in your normal browser, then copy its cookies
into the saved-session file that posting already uses.

Firefox is the easiest source — its `cookies.sqlite` is unencrypted, so no
system-keyring access is needed:

```bash
uv sync --extra session_import
media-sharing import-session tiktok my_tiktok --browser firefox --verify
```

- `--browser` accepts `firefox`, `chrome`, `chromium`, `brave`, `edge`, ... Only
  the cookie store is read; nothing is written to the source browser.
- `--profile PATH` points at a non-default profile directory or a `cookies.sqlite`.
- `--verify` opens the uploader headless with the imported session and reports
  whether you're logged in.
- Chrome-family stores are encrypted; if decryption fails, log in to Firefox or
  export cookies with a Cookie-Editor extension and pass `--from-file export.json`.

Re-run this whenever the session expires (posts then fail with a login redirect).

### X (Twitter) — `x_playwright`
- **Auth:** just your normal X login. No developer app, **no API cost**.
- Selectors are in `x_playwright.DEFAULTS`; override per account in
  `options` if X's UI changes.

### TikTok — `tiktok_playwright`
- **Auth:** your normal TikTok login. No developer app, no audit.
- Targets the TikTok Studio uploader; override selectors in `options` if the UI
  changes. See §3 for when you'd want the API instead.

### Kwai — `kwai_mobile` (legacy `kwai_playwright`)

Kwai is **app-only** now: use the mobile backend in §6. The `kwai_playwright`
module is kept for reference but is out of the default chain (Kwai Studio has no
usable upload flow for most accounts). If you still want to try it, pin
`backend: kwai_playwright` on the account and calibrate it with
`media-sharing discover` + the "Calibrating selectors" section below.

#### Calibrating selectors (X, TikTok, Kwai)
The web UIs change and vary by locale, so the shipped defaults rarely work as-is.
Use the interactive discover tool:

```bash
media-sharing discover <account>          # uses the account's backend defaults
media-sharing discover my_kwai --url https://studio.kwai.com/
```

It opens a **headed** browser. Log in, navigate to the upload screen, reveal the
file picker and caption box, then press Enter. It prints candidate
`input[type=file]`, caption editors and buttons (with `data-e2e` / `data-testid`
/ `aria-label`) and saves `debug/<account>.html` + `.png`.

Copy the best matches into the account's `options`:

```yaml
  - name: my_kwai
    platform: kwai
    enabled: true
    options:
      upload_url: "https://studio.kwai.com/<the-upload-route-you-found>"
      file_input: "input[type=file]"
      caption_input: "div[contenteditable='true']"
      publish_button: "button[...]"
      headless: false        # keep false while calibrating
```

`success_url_glob` is optional. For Kwai leave it unset: login opens the browser
and waits until you press Enter, then saves the session. (A catch-all glob like
`**/*` is ignored — it would match before you finish logging in.) X and TikTok
ship real post-login globs so their logins wait automatically. Also note
`media-sharing discover` reuses the saved session, so run `login` first and the
debug browser will already be signed in.

Then `media-sharing post ./clip.mp4 --account my_kwai`. When it works, set
`headless: true`.

> **Kwai caveat:** because Kwai is designed around its mobile app, Kwai now
> ships as the **`kwai_mobile`** backend, which drives the official Android app on
> a USB-connected phone via ADB + Maestro — see [`docs/mobile.md`](mobile.md).
> The chain in `config/platforms.yaml` is `kwai: [kwai_mobile]`. The legacy
> `kwai_playwright` backend still exists but is no longer in the chain (Studio
> has no usable upload flow for most accounts).

---

## 2. Bluesky — `bluesky_api`

No developer app. Bluesky uses **app passwords**.

1. Create/log into your Bluesky account.
2. Go to **Settings → App Passwords** (`https://bsky.app/settings/app-passwords`)
   and create one.
3. Copy `.env.example` to `.env` and set:
   ```
   BLUESKY_HANDLE=my.bsky.social
   BLUESKY_APP_PASSWORD=xxxx-xxxx-xxxx-xxxx
   ```
4. `config/accounts.yaml` already references these via `env:`. Set
   `enabled: true`.

Free, no review, posts are public immediately.

---

## 3. TikTok — `tiktok_playwright` (default) or `tiktok_api`

### A. Browser session — `tiktok_playwright` (recommended for your own feed)
Posts to *your* account's feed without any developer app, ToS/Privacy URLs, app
review, or audit:

```bash
media-sharing login tiktok my_tiktok
```

Sign in (and pass any captcha/2FA). The session is saved and reused. This is the
default in `config/platforms.yaml` (`tiktok: [tiktok_playwright, tiktok_api]`).

If TikTok blocks the automated login ("Maximum number of attempts reached"), use
**Importing a session from a real browser** in §1 instead —
`media-sharing import-session tiktok <account> --browser firefox --verify`.

- Selectors/URLs are in `tiktok_playwright.DEFAULTS` and target the TikTok Studio
  uploader; override per account in `options` if the UI changes.
- Keep volume low; automation carries ToS/ban risk.

### B. Official Content Posting API — `tiktok_api`
Only needed if you want programmatic posting with an audited, public app — for
example an app that posts on behalf of *many* users. The developer-app form
(ToS URL, Privacy Policy URL, Platforms, …) exists because that API is designed
for third-party apps serving other creators. If your goal is simply posting to
your own feed, use option A.

If you do go this route:
1. Create a TikTok developer account and an app; add the Content Posting API.
2. Request `video.upload` (draft, works unaudited) and/or `video.publish`
   (direct, requires passing TikTok's **audit**).
3. OAuth your TikTok user and set `TIKTOK_ACCESS_TOKEN` + `TIKTOK_OPEN_ID`.
4. Pin `backend: tiktok_api` on the account (or reorder the chain).

---

## 4. X via official API — `x_api` (optional, costs money)

Only if you specifically want the API instead of the browser session.
1. Apply for an **X developer account**; note the 2026 pay-per-use pricing
   (~$0.015/post; ~$0.20 if the caption contains a URL).
2. Create an app with **OAuth 1.0a** user-context keys.
3. Set `X_API_KEY`, `X_API_SECRET`, `X_ACCESS_TOKEN`, `X_ACCESS_SECRET` in
   `.env`, then pin `backend: x_api` on the account (or change the chain in
   `config/platforms.yaml`).

---

## 5. Aggregator — `aggregator` (optional)

One vendor account posts to many platforms (their apps are audited). **None
support Kwai.**

1. Sign up with a vendor (e.g. Upload-Post, Ayrshare, Blotato).
2. In the vendor dashboard, connect each social account via their OAuth.
3. Set `AGGREGATOR_API_KEY` and `AGGREGATOR_PROFILE` in `.env`.
4. On the account in `accounts.yaml`:
   ```yaml
   backend: aggregator
   options: { vendor: upload-post }   # or ayrshare
   ```
   - `upload-post`: direct file upload, no public URL needed.
   - `ayrshare`: needs a public media URL (set `MEDIA_PUBLIC_BASE_URL`) and BYO X
     credentials.

---

## 6. Mobile backends — `*_mobile` (Android app via ADB + Maestro)

Used where no web upload flow exists. Kwai and Instagram are **mobile only**;
TikTok uses `tiktok_mobile` as a last fallback.

No session file is created: the app stays logged in on the phone. After adding
the account to `config/accounts.yaml`:

```bash
media-sharing devices          # list connected ADB devices
media-sharing mobile-setup <account>   # verify tooling/device/app and open the app
media-sharing inspect <account>        # dump UI + screenshot to calibrate the flow
```

Then author the flow at `config/mobile/<platform>.yaml` (see the shipped
PLACEHOLDER flows) using `maestro studio`. Full setup, calibration and
troubleshooting: [`docs/mobile.md`](mobile.md).

---

## Quick checklist for a fresh setup

```bash
uv sync --all-extras                       # base + optional backends + dev tools
uv run playwright install chromium         # browser binaries for *_playwright backends
cp .env.example .env                       # fill in any API keys you'll use

# accounts.yaml: add each account, set enabled: true
uv run media-sharing login x my_x          # browser-session platforms
uv run media-sharing login tiktok my_tiktok

# mobile platforms (Kwai, Instagram, TikTok fallback): connect a USB phone first
uv run media-sharing devices               # list ADB devices
uv run media-sharing mobile-setup <account>   # verify tooling/device/app

uv run media-sharing accounts              # verify
uv run media-sharing list-backends         # marks (browser login) / (mobile app)
uv run media-sharing post ./test.mp4       # smoke test
```
