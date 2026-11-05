import hashlib
import io
import json
import os
import threading
import time
import urllib.parse
import urllib.request
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from minitok import tiktok_api
from minitok.algorithm.learner import learn_from_export_file

EXPORT = {
    "Your Activity": {
        "Watch History": {"VideoList": [
            {"Date": f"2026-01-01 10:{m:02d}:00", "Link": f"https://www.tiktokv.com/share/video/{100 + m}/"}
            for m in range(20)
        ]},
        "Searches": {"SearchList": [{"Date": "2026-01-01 09:00:00", "SearchTerm": "pasta recipe"}]},
    },
    "Likes and Favorites": {"Like List": {"ItemFavoriteList": [
        {"date": "2026-01-01 10:05:30", "link": "https://www.tiktokv.com/share/video/105/"}]}},
}


def export_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("activity/user_data_tiktok.json", json.dumps(EXPORT))
    return buf.getvalue()


class FakeTikTok:
    """Just enough of Login Kit + Data Portability to run the real client against."""

    def __init__(self, polls_until_ready=2):
        self.challenge = None
        self.codes = {}
        self.polls_until_ready = polls_until_ready
        self.checks = 0
        self.calls = []
        self.access = "act.1"
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _json(self, obj, status=200):
                body = json.dumps(obj).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)

            def _authed(self):
                return self.headers.get("Authorization") == f"Bearer {fake.access}"

            def do_GET(self):  # noqa: N802
                u = urllib.parse.urlparse(self.path)
                q = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
                fake.calls.append(u.path)
                if u.path == "/v2/auth/authorize/":
                    assert q["response_type"] == "code" and q["code_challenge_method"] == "S256"
                    assert q["client_key"] == "ck" and "portability.activity.single" in q["scope"]
                    fake.challenge = q["code_challenge"]
                    code = "code-123"
                    fake.codes[code] = q["redirect_uri"]
                    self.send_response(302)
                    self.send_header("Location", f"{q['redirect_uri']}?code={code}&state={q['state']}&scopes=x")
                    self.end_headers()
                elif u.path == "/v2/user/info/":
                    self._json({"data": {"user": {"display_name": "Luca"}}, "error": {"code": "ok"}})
                else:
                    self.send_error(404)

            def do_POST(self):  # noqa: N802
                u = urllib.parse.urlparse(self.path)
                raw = self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode()
                fake.calls.append(u.path)
                if u.path == "/v2/oauth/token/":
                    f = {k: v[0] for k, v in urllib.parse.parse_qs(raw).items()}
                    assert f["client_secret"] == "cs"
                    if f["grant_type"] == "refresh_token":
                        assert f["refresh_token"] == "rft.1"
                        fake.access = "act.2"
                        return self._json({"access_token": "act.2", "refresh_token": "rft.2", "expires_in": 86400,
                                           "open_id": "u1", "scope": "x"})
                    # TikTok wants hex(sha256(verifier)), not base64url
                    if hashlib.sha256(f["code_verifier"].encode()).hexdigest() != fake.challenge:
                        return self._json({"error": "invalid_grant", "error_description": "PKCE mismatch"}, 400)
                    assert fake.codes.get(f["code"]) == f["redirect_uri"]
                    return self._json({"access_token": "act.1", "refresh_token": "rft.1", "expires_in": 86400,
                                       "refresh_expires_in": 31536000, "open_id": "u1", "scope": "x"})
                if not self._authed():
                    return self._json({"error": {"code": "access_token_invalid", "message": "bad token"}}, 401)
                body = json.loads(raw or "{}")
                if u.path == "/v2/user/data/add/":
                    assert body == {"data_format": "json", "category_selection_list": ["activity"]}
                    return self._json({"data": {"request_id": 777}, "error": {"code": "ok"}})
                if u.path == "/v2/user/data/check/":
                    fake.checks += 1
                    status = "downloading" if fake.checks > fake.polls_until_ready else "pending"
                    return self._json({"data": {"request_id": body["request_id"], "status": status},
                                       "error": {"code": "ok"}})
                if u.path == "/v2/user/data/download/":
                    data = export_zip()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/zip")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    return
                self.send_error(404)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()


@pytest.fixture
def fake(monkeypatch):
    f = FakeTikTok()
    monkeypatch.setattr(tiktok_api, "AUTH_URL", f.base + "/v2/auth/authorize/")
    monkeypatch.setattr(tiktok_api, "API_BASE", f.base)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    yield f
    f.server.shutdown()


def browser(url):
    """Stands in for the person's browser: open the auth page and follow TikTok's redirect home."""
    urllib.request.urlopen(url, timeout=10).read()


def configured_state():
    s = tiktok_api.ConnectState(client_key="ck", client_secret="cs")
    s.save()
    return s


def test_pkce_challenge_is_hex_sha256():
    v = tiktok_api.make_verifier()
    assert 43 <= len(v) <= 128 and set(v) <= set(tiktok_api.VERIFIER_CHARS)
    assert tiktok_api.code_challenge("abc") == hashlib.sha256(b"abc").hexdigest()


def test_full_connect_flow_downloads_data_and_learns(fake, tmp_path):
    state = configured_state()
    token = tiktok_api.login(state, open_browser=browser, timeout=10)
    assert token.access_token == "act.1" and token.open_id == "u1"
    assert tiktok_api.display_name(state) == "Luca"

    messages = []
    path = tiktok_api.sync(state, progress=messages.append, poll=0.01, sleep=lambda s: None)
    assert path is not None and path.exists() and zipfile.is_zipfile(path)
    assert fake.checks == 3  # pending, pending, downloading
    assert any("preparing" in m for m in messages)
    assert state.pending_request_id is None and state.last_sync > 0

    # saved with owner-only permissions, and reloads intact
    if os.name == "posix":
        assert (tiktok_api.ConnectState.path().stat().st_mode & 0o777) == 0o600
    again = tiktok_api.ConnectState.load()
    assert again.token.refresh_token == "rft.1" and again.display_name == "Luca"

    export, result = learn_from_export_file(path)
    assert len(export.watched) == 20 and len(export.liked) == 1
    assert result.interests.get("#pasta", 0) > 0  # from the search history


def test_pending_request_survives_restart(fake):
    state = configured_state()
    tiktok_api.login(state, open_browser=browser, timeout=10)
    stop = {"n": 0}

    def should_stop():
        stop["n"] += 1
        return stop["n"] > 2  # stop while TikTok is still preparing

    assert tiktok_api.sync(state, should_stop=should_stop, poll=0.01, sleep=lambda s: None) is None
    assert tiktok_api.ConnectState.load().pending_request_id == 777
    resumed = tiktok_api.ConnectState.load()
    assert tiktok_api.sync(resumed, poll=0.01, sleep=lambda s: None).exists()
    assert fake.calls.count("/v2/user/data/add/") == 1  # resumed, not re-requested


def test_expired_access_token_is_refreshed(fake):
    state = configured_state()
    tiktok_api.login(state, open_browser=browser, timeout=10)
    state.token.expires_at = time.time() - 10
    assert tiktok_api.access_token(state) == "act.2"
    assert tiktok_api.ConnectState.load().token.refresh_token == "rft.2"


def test_state_mismatch_is_rejected(fake, monkeypatch):
    state = configured_state()

    def evil_browser(url):
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(url).query))
        urllib.request.urlopen(f"{q['redirect_uri']}?code=x&state=forged", timeout=10).read()

    with pytest.raises(tiktok_api.TikTokError, match="state"):
        tiktok_api.login(state, open_browser=evil_browser, timeout=10)


def test_denied_access_and_api_errors(fake):
    state = configured_state()

    def denying_browser(url):
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(url).query))
        urllib.request.urlopen(f"{q['redirect_uri']}?error=access_denied&state={q['state']}", timeout=10).read()

    with pytest.raises(tiktok_api.TikTokError, match="access_denied"):
        tiktok_api.login(state, open_browser=denying_browser, timeout=10)

    state.token = tiktok_api.Token(access_token="wrong", refresh_token="", expires_at=time.time() + 999)
    with pytest.raises(tiktok_api.TikTokError, match="bad token"):
        tiktok_api.request_data(state)


def test_not_configured():
    with pytest.raises(tiktok_api.TikTokError, match="developer app"):
        tiktok_api.login(tiktok_api.ConnectState())
