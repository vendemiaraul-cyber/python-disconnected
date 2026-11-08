"""Tiny urllib helpers. Standard library only, so the app runs without pip."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Iterator

USER_AGENT = "llmstream/0.1 (+https://github.com/vendemiaraul-cyber/python-disconnected)"


class HTTPError(Exception):
    def __init__(self, status: int, message: str, url: str = ""):
        super().__init__(f"HTTP {status}: {message}")
        self.status = status
        self.message = message
        self.url = url


def _request(url: str, method: str = "GET", data: Any = None, headers: dict | None = None) -> urllib.request.Request:
    hdrs = {"User-Agent": USER_AGENT}
    body = None
    if data is not None:
        body = json.dumps(data).encode("utf-8")
        hdrs["Content-Type"] = "application/json"
    hdrs.update(headers or {})
    return urllib.request.Request(url, data=body, method=method, headers=hdrs)


def open_url(url: str, method: str = "GET", data: Any = None, headers: dict | None = None, timeout: float = 30):
    """Open a URL, turning urllib errors into HTTPError with the server's message."""
    try:
        return urllib.request.urlopen(_request(url, method, data, headers), timeout=timeout)
    except urllib.error.HTTPError as exc:
        try:
            detail = exc.read().decode("utf-8", "replace")[:500]
            try:
                parsed = json.loads(detail)
                detail = parsed.get("error") or parsed.get("message") or detail
                if isinstance(detail, dict):
                    detail = detail.get("message") or json.dumps(detail)
            except (ValueError, AttributeError):
                pass
        except Exception:  # noqa: BLE001
            detail = exc.reason
        raise HTTPError(exc.code, str(detail), url) from None


def get_json(url: str, headers: dict | None = None, timeout: float = 30) -> Any:
    with open_url(url, headers=headers, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def post_json(url: str, data: Any, headers: dict | None = None, timeout: float = 30) -> Any:
    with open_url(url, "POST", data, headers, timeout) as resp:
        raw = resp.read().decode("utf-8")
        return json.loads(raw) if raw.strip() else {}


def head(url: str, headers: dict | None = None, timeout: float = 20) -> tuple[int, dict]:
    """HEAD a URL (following redirects). Returns (status, headers); status is the HTTP error code on failure."""
    try:
        with open_url(url, "HEAD", headers=headers, timeout=timeout) as resp:
            return resp.status, {k.lower(): v for k, v in resp.headers.items()}
    except HTTPError as exc:
        return exc.status, {}


def stream_lines(resp) -> Iterator[str]:
    """Yield decoded lines from a streaming HTTP response as they arrive."""
    for raw in resp:
        line = raw.decode("utf-8", "replace").rstrip("\r\n")
        if line:
            yield line
