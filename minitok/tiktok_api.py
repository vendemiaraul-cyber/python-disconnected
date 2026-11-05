"""Official TikTok APIs: Login Kit (desktop) + Data Portability API.

"Continue with TikTok" -> the person approves in their browser -> MiniTok asks
TikTok for their activity data (watch history, likes, favourites, searches,
shares) -> waits while TikTok prepares it -> downloads the .zip, which the
existing export parser and learner read like a manual "Download your data".

Needs a TikTok developer app (https://developers.tiktok.com) with Login Kit and
the Data Portability API approved, and the redirect URI
``http://127.0.0.1:*/callback/`` registered. Its client key and secret go in
``tiktok.json`` in the MiniTok data folder (the settings dialog writes it).

Docs: developers.tiktok.com/doc/login-kit-desktop and
developers.tiktok.com/doc/data-portability-api-get-started
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from dataclasses import asdict, dataclass, field
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Callable

from .config import data_dir

# overridable so the tests can run the whole flow against a local fake TikTok
AUTH_URL = os.environ.get("MINITOK_TIKTOK_AUTH_URL", "https://www.tiktok.com/v2/auth/authorize/")
API_BASE = os.environ.get("MINITOK_TIKTOK_API_BASE", "https://open.tiktokapis.com").rstrip("/")

DEFAULT_SCOPES = "user.info.basic,portability.activity.single"
REDIRECT_PATH = "/callback/"
VERIFIER_CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~"
LOGIN_TIMEOUT = 300.0  # seconds to wait for the person to approve in the browser

CALLBACK_PAGE = """<!doctype html><meta charset="utf-8"><title>MiniTok</title>
<body style="font-family:-apple-system,Segoe UI,Roboto,sans-serif;background:#000;color:#fff;
display:grid;place-items:center;height:100vh;margin:0;text-align:center">
<div><div style="font-size:44px">{icon}</div><h2>{title}</h2>
<p style="color:#aaa">{text}</p></div></body>"""


class TikTokError(RuntimeError):
    pass


@dataclass
class Token:
    access_token: str
    refresh_token: str
    open_id: str = ""
    scope: str = ""
    expires_at: float = 0.0
    refresh_expires_at: float = 0.0

    @property
    def expired(self) -> bool:
        return time.time() > self.expires_at - 120

    @classmethod
    def from_response(cls, data: dict[str, Any]) -> "Token":
        now = time.time()
        return cls(
            access_token=data["access_token"],
            refresh_token=data.get("refresh_token", ""),
            open_id=data.get("open_id", ""),
            scope=data.get("scope", ""),
            expires_at=now + float(data.get("expires_in") or 86400),
            refresh_expires_at=now + float(data.get("refresh_expires_in") or 365 * 86400),
        )


@dataclass
class ConnectState:
    """Everything about the TikTok connection, kept in ``tiktok.json`` (owner-only permissions)."""

    client_key: str = ""
    client_secret: str = ""
    scopes: str = DEFAULT_SCOPES
    token: Token | None = None
    display_name: str = ""
    pending_request_id: int | None = None
    requested_at: float = 0.0
    last_sync: float = 0.0
    export_path: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def configured(self) -> bool:
        return bool(self.client_key and self.client_secret)

    @staticmethod
    def path() -> Path:
        return data_dir() / "tiktok.json"

    def save(self) -> None:
        path = self.path()
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
        try:
            os.chmod(tmp, 0o600)  # it holds the app secret and the person's tokens
        except OSError:
            pass
        tmp.replace(path)

    @classmethod
    def load(cls) -> "ConnectState":
        try:
            data = json.loads(cls.path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()
        token = data.pop("token", None)
        known = set(cls.__dataclass_fields__)
        state = cls(**{k: v for k, v in data.items() if k in known})
        if token:
            state.token = Token(**{k: v for k, v in token.items() if k in Token.__dataclass_fields__})
        return state


# -- PKCE (TikTok wants the *hex* SHA-256 of the verifier, not base64url) -------------

def make_verifier(length: int = 64) -> str:
    return "".join(secrets.choice(VERIFIER_CHARS) for _ in range(length))


def code_challenge(verifier: str) -> str:
    return hashlib.sha256(verifier.encode("ascii")).hexdigest()


def authorize_url(client_key: str, scopes: str, redirect_uri: str, state: str, challenge: str) -> str:
    query = {
        "client_key": client_key,
        "response_type": "code",
        "scope": scopes,
        "redirect_uri": redirect_uri,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    return f"{AUTH_URL}?{urllib.parse.urlencode(query)}"


# -- HTTP helpers -----------------------------------------------------------------------

def _request(method: str, url: str, *, form: dict | None = None, body: dict | None = None,
             token: str | None = None, raw: bool = False, timeout: float = 60.0) -> Any:
    headers = {"Cache-Control": "no-cache"}
    data = None
    if form is not None:
        data = urllib.parse.urlencode(form).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    elif body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = resp.read()
            ctype = resp.headers.get("Content-Type", "")
    except urllib.error.HTTPError as exc:
        payload, ctype = exc.read(), exc.headers.get("Content-Type", "")
        if raw and "json" not in ctype:
            raise TikTokError(f"TikTok answered HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise TikTokError(f"Couldn't reach TikTok: {exc.reason}") from exc
    if raw and "json" not in ctype:
        return payload
    try:
        result = json.loads(payload.decode("utf-8") or "{}")
    except ValueError as exc:
        raise TikTokError("TikTok sent an unexpected answer") from exc
    _raise_for_error(result)
    return result


def _raise_for_error(result: dict[str, Any]) -> None:
    # OAuth errors: {"error": "invalid_grant", "error_description": "..."}
    if isinstance(result.get("error"), str) and result["error"]:
        raise TikTokError(result.get("error_description") or result["error"])
    # v2 API errors: {"error": {"code": "ok" | "...", "message": "..."}}
    err = result.get("error")
    if isinstance(err, dict) and err.get("code") not in (None, "", "ok"):
        raise TikTokError(err.get("message") or err["code"])


# -- Login Kit ------------------------------------------------------------------------------

class _CallbackServer(HTTPServer):
    result: dict[str, str] | None = None


class _CallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path.rstrip("/") != REDIRECT_PATH.rstrip("/"):
            self.send_error(404)
            return
        params = {k: v[0] for k, v in urllib.parse.parse_qs(parsed.query).items()}
        self.server.result = params  # type: ignore[attr-defined]
        ok = "code" in params
        page = CALLBACK_PAGE.format(
            icon="✅" if ok else "⚠️",
            title="You're connected" if ok else "Not connected",
            text="You can close this tab and go back to MiniTok." if ok
            else "TikTok didn't grant access. You can close this tab and try again in MiniTok.",
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(page)))
        self.end_headers()
        self.wfile.write(page)

    def log_message(self, *args: Any) -> None:
        pass


def login(state: ConnectState, open_browser: Callable[[str], Any] | None = None,
          timeout: float = LOGIN_TIMEOUT, should_stop: Callable[[], bool] | None = None) -> Token:
    """Run the desktop Login Kit flow and store the token in ``state``."""
    open_browser = open_browser or webbrowser.open
    if not state.configured:
        raise TikTokError("MiniTok isn't linked to a TikTok developer app yet")
    server = _CallbackServer(("127.0.0.1", 0), _CallbackHandler)
    server.timeout = 0.5
    redirect_uri = f"http://127.0.0.1:{server.server_address[1]}{REDIRECT_PATH}"
    verifier, csrf = make_verifier(), secrets.token_urlsafe(24)
    url = authorize_url(state.client_key, state.scopes, redirect_uri, csrf, code_challenge(verifier))
    worker = threading.Thread(target=open_browser, args=(url,), daemon=True)
    worker.start()
    deadline = time.monotonic() + timeout
    try:
        while server.result is None:
            if time.monotonic() > deadline:
                raise TikTokError("Timed out waiting for TikTok approval")
            if should_stop and should_stop():
                raise TikTokError("Cancelled")
            server.handle_request()
    finally:
        server.server_close()
    params = server.result
    if params.get("state") != csrf:
        raise TikTokError("Login answer didn't match this request (state mismatch) - try again")
    if "code" not in params:
        raise TikTokError(params.get("error_description") or params.get("error") or "TikTok didn't grant access")
    result = _request("POST", f"{API_BASE}/v2/oauth/token/", form={
        "client_key": state.client_key,
        "client_secret": state.client_secret,
        "code": params["code"],
        "grant_type": "authorization_code",
        "redirect_uri": redirect_uri,
        "code_verifier": verifier,
    })
    state.token = Token.from_response(result)
    state.save()
    return state.token


def access_token(state: ConnectState) -> str:
    """A valid access token, refreshed when it has expired."""
    tok = state.token
    if tok is None:
        raise TikTokError("Not connected to TikTok")
    if tok.expired:
        if not tok.refresh_token or time.time() > tok.refresh_expires_at:
            state.token = None
            state.save()
            raise TikTokError("The TikTok connection expired - tap Continue with TikTok again")
        result = _request("POST", f"{API_BASE}/v2/oauth/token/", form={
            "client_key": state.client_key,
            "client_secret": state.client_secret,
            "grant_type": "refresh_token",
            "refresh_token": tok.refresh_token,
        })
        state.token = Token.from_response(result)
        state.save()
    return state.token.access_token  # type: ignore[union-attr]


def display_name(state: ConnectState) -> str:
    result = _request("GET", f"{API_BASE}/v2/user/info/?fields=open_id,display_name", token=access_token(state))
    name = ((result.get("data") or {}).get("user") or {}).get("display_name") or ""
    state.display_name = name
    state.save()
    return name


# -- Data Portability API ---------------------------------------------------------------------

def request_data(state: ConnectState, categories: tuple[str, ...] = ("activity",)) -> int:
    result = _request("POST", f"{API_BASE}/v2/user/data/add/?fields=request_id", token=access_token(state),
                      body={"data_format": "json", "category_selection_list": list(categories)})
    request_id = (result.get("data") or {}).get("request_id")
    if not request_id:
        raise TikTokError("TikTok didn't return a request id")
    state.pending_request_id = int(request_id)
    state.requested_at = time.time()
    state.save()
    return state.pending_request_id


def check_status(state: ConnectState, request_id: int) -> str:
    result = _request("POST", f"{API_BASE}/v2/user/data/check/?fields=request_id,status",
                      token=access_token(state), body={"request_id": request_id})
    return str((result.get("data") or {}).get("status") or "pending")


def download(state: ConnectState, request_id: int, dest: Path) -> Path:
    payload = _request("POST", f"{API_BASE}/v2/user/data/download/", token=access_token(state),
                       body={"request_id": request_id}, raw=True, timeout=600)
    if not isinstance(payload, (bytes, bytearray)) or not payload.startswith(b"PK"):
        raise TikTokError("TikTok's download wasn't a .zip file")
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".part")
    tmp.write_bytes(payload)
    tmp.replace(dest)
    return dest


def sync(state: ConnectState, progress: Callable[[str], None] = lambda _m: None,
         should_stop: Callable[[], bool] = lambda: False, poll: float = 20.0, max_poll: float = 300.0,
         sleep: Callable[[float], None] = time.sleep) -> Path | None:
    """Request (or resume) a data export and wait until it can be downloaded.

    Returns the downloaded .zip, or None if stopped; the pending request is kept
    in ``state`` so a later call (e.g. the next app start) picks it up again.
    """
    rid = state.pending_request_id
    if rid is None:
        progress("Asking TikTok for your watch history…")
        rid = request_data(state)
    delay = poll
    while not should_stop():
        status = check_status(state, rid)
        if status == "downloading":
            progress("Downloading your data from TikTok…")
            path = download(state, rid, data_dir() / "tiktok_data" / f"{rid}.zip")
            state.pending_request_id = None
            state.last_sync = time.time()
            state.export_path = str(path)
            state.save()
            return path
        if status in ("expired", "cancelled"):
            state.pending_request_id = None
            state.save()
            raise TikTokError(f"TikTok's data request was {status} - tap Continue with TikTok to start again")
        waited = time.time() - state.requested_at
        progress("TikTok is preparing your data" + (f" ({int(waited // 60)} min so far)" if waited >= 60 else "")
                 + ". You can keep watching - MiniTok will finish by itself.")
        end = time.monotonic() + delay
        while time.monotonic() < end and not should_stop():
            sleep(max(0.0, min(1.0, end - time.monotonic())))
        delay = min(max_poll, delay * 1.5)
    return None
