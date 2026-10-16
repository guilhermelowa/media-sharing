"""Command-line interface.

    media-sharing run                         # watch inbox/ continuously
    media-sharing post <file>                 # publish one file now
    media-sharing login <platform> <account>  # capture a browser session
    media-sharing list-backends               # show registered publisher backends
    media-sharing accounts                    # show configured accounts
"""

from __future__ import annotations

import logging
from pathlib import Path

import typer

from . import backends
from .backends.base import PublishError
from .config import Settings, get_settings
from .pipeline import backend_chain, fan_out
from .session_import import import_session as import_browser_session
from .session_import import verify_session
from .state import StateStore
from .watcher import build_item, run_watcher

app = typer.Typer(help="Automated multi-platform media publishing.", no_args_is_help=True)


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def _resolve_account(settings: Settings, account: str):
    matches = settings.accounts_for(name=account)
    if not matches:
        raise typer.BadParameter(f"No account named '{account}'")
    return matches[0]


def _login_backend(settings: Settings, account) -> str:
    """Pick the backend to use for interactive login (first session-based one)."""
    for name in backend_chain(settings, account):
        if name in backends.LOGIN_BACKENDS:
            return name
    if account.backend in backends.LOGIN_BACKENDS:
        return account.backend
    raise typer.BadParameter(
        f"No login-capable backend configured for '{account.name}'. "
        f"Set its chain (config/platforms.yaml) to one of {sorted(backends.LOGIN_BACKENDS)} "
        "or pin account.backend."
    )


def _mobile_backend(settings: Settings, account) -> str:
    """Pick the ADB + Maestro backend configured for this account."""
    for name in backend_chain(settings, account):
        if name in backends.MOBILE_BACKENDS:
            return name
    if account.backend in backends.MOBILE_BACKENDS:
        return account.backend
    raise typer.BadParameter(
        f"No mobile backend configured for '{account.name}'. Set its chain "
        f"(config/platforms.yaml) to one of {sorted(backends.MOBILE_BACKENDS)} "
        "or pin account.backend."
    )


@app.command()
def run() -> None:
    """Watch the inbox folder and publish as files arrive."""
    _setup_logging()
    settings = get_settings()
    store = StateStore(settings.paths.state_db)
    run_watcher(settings, store)


@app.command()
def post(file: Path, account: str = typer.Option(None, help="Limit to one account.")) -> None:
    """Publish a single media file immediately."""
    _setup_logging()
    settings = get_settings()
    store = StateStore(settings.paths.state_db)
    item = build_item(file)
    results = fan_out(item, settings, store, account_name=account)
    for r in results:
        typer.echo(f"{r.platform.value:10} {r.status.value:16} {r.backend:16} {r.detail or ''}")


@app.command()
def login(platform: str, account: str) -> None:
    """Capture and persist a browser session for a session-based account.

    Example: media-sharing login x my_x
    """
    _setup_logging()
    settings = get_settings()
    acct = _resolve_account(settings, account)
    if acct.platform.value != platform:
        raise typer.BadParameter(
            f"Account '{account}' is platform '{acct.platform.value}', not '{platform}'")
    backend_name = _login_backend(settings, acct)
    typer.echo(f"Logging in via {backend_name}...")
    backends.interactive_login(backend_name, acct, settings)


@app.command("import-session")
def import_session(
    platform: str,
    account: str,
    browser: str = typer.Option("firefox", help="Source browser (firefox, chrome, ...)."),
    profile: str = typer.Option(None, help="Browser profile dir or cookies file path."),
    from_file: str = typer.Option(None, help="Cookie-Editor JSON export (bypasses browser_cookie3)."),
    verify: bool = typer.Option(False, help="Open the uploader to check the login took."),
) -> None:
    """Import a logged-in browser session instead of automating the login form.

    Log in to the platform manually in a real browser, then run this to copy its
    cookies into sessions/<account>.json. Example:

        media-sharing import-session tiktok my_tiktok --browser firefox
    """
    _setup_logging()
    settings = get_settings()
    acct = _resolve_account(settings, account)
    if acct.platform.value != platform:
        raise typer.BadParameter(
            f"Account '{account}' is platform '{acct.platform.value}', not '{platform}'")
    backend_name = _login_backend(settings, acct)
    domains, required = backends.login_cookie_metadata(backend_name)
    try:
        dest, found, missing = import_browser_session(
            settings, acct, browser=browser, profile=profile,
            export_file=from_file, domains=domains, required_names=required,
        )
    except PublishError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(f"Wrote session to {dest}")
    typer.echo(f"Login cookies found: {', '.join(found) or 'none'}")
    if missing:
        typer.echo(f"warning: missing expected cookies: {', '.join(missing)}", err=True)

    if verify:
        config = backends.browser_config(backend_name, acct)
        logged_in, url = verify_session(settings, acct, config)
        status = "logged in" if logged_in else "NOT logged in"
        typer.echo(f"verify: {status} ({url})")
        if not logged_in:
            raise typer.Exit(code=1)


@app.command()
def discover(account: str, url: str = typer.Option(None, help="Start URL override."),
             backend: str = typer.Option(None, help="Backend to use for defaults."),
             upload: str = typer.Option(None, help="Local media file to select, to reveal the editor."),
             ) -> None:
    """Open a headed browser to find selectors for a session-based account.

    Navigate to the upload screen, reveal the file picker/caption box, press
    Enter, then copy the printed matches into the account's options. Use
    --upload to load a file automatically and reach the caption/Post stage.
    """
    settings = get_settings()
    acct = _resolve_account(settings, account)
    backend_name = backend or _login_backend(settings, acct)
    backends.discover(backend_name, acct, settings, start_url=url, upload_path=upload)


@app.command()
def devices() -> None:
    """List connected ADB devices and which one is selected."""
    try:
        backends.mobile.require_adb()
    except PublishError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    found = backends.mobile.list_devices()
    if not found:
        typer.echo("No devices. Connect a phone over USB with debugging enabled.")
        return
    for serial in found:
        mark = "  (selected)" if len(found) == 1 else ""
        typer.echo(f"{serial}{mark}")
    if len(found) > 1:
        typer.echo("More than one device — set options.device on the account.")


@app.command("mobile-setup")
def mobile_setup(account: str) -> None:
    """Verify adb/Maestro/device/app and open the app for a mobile account.

    The mobile analog of `login`: it persists nothing — it just confirms the
    phone is ready and the app is installed/logged in for `<account>`.
    """
    _setup_logging()
    settings = get_settings()
    acct = _resolve_account(settings, account)
    backend_name = _mobile_backend(settings, acct)
    config = backends.mobile_config(backend_name, acct)
    try:
        device = backends.mobile.validate(acct, config)
    except PublishError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(f"device: {device}")
    typer.echo(f"app:    {config.app_id} (installed)")
    backends.mobile.launch_app(device, config.app_id)
    typer.echo("Opened the app on the phone. Confirm you are logged in, then "
               "press Enter (nothing is persisted).")
    input()


@app.command()
def inspect(account: str) -> None:
    """Dump the phone's current UI + screenshot to debug/ to author the flow.

    The mobile analog of `discover`: it prints a hint to run `maestro studio`.
    """
    _setup_logging()
    settings = get_settings()
    acct = _resolve_account(settings, account)
    backend_name = _mobile_backend(settings, acct)
    config = backends.mobile_config(backend_name, acct)
    try:
        backends.mobile.require_tools()
        device = backends.mobile.resolve_device(config)
    except PublishError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    debug = settings.paths.debug
    debug.mkdir(parents=True, exist_ok=True)
    xml = backends.mobile.dump_ui(device, debug / f"{acct.name}-ui.xml")
    shot = backends.mobile.screenshot(device, debug / f"{acct.name}.png")
    typer.echo(f"device: {device}")
    typer.echo(f"Saved {xml} and {shot}")
    typer.echo("Author the flow with `maestro studio`, then copy selectors into "
               f"config/mobile/{acct.platform.value}.yaml")


@app.command("list-backends")
def list_backends() -> None:
    """List registered publisher backends (login/mobile ones are marked)."""
    for name in backends.available():
        if name in backends.LOGIN_BACKENDS:
            mark = "  (browser login)"
        elif name in backends.MOBILE_BACKENDS:
            mark = "  (mobile app)"
        else:
            mark = ""
        typer.echo(f"{name}{mark}")


@app.command()
def accounts() -> None:
    """List configured accounts."""
    for a in get_settings().accounts:
        flag = "on " if a.enabled else "off"
        typer.echo(f"[{flag}] {a.name:20} {a.platform.value:10} {a.backend or '(chain)'}")


if __name__ == "__main__":
    app()
