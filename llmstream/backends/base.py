"""Common shape for every server-side backend. Each chat() yields small event dicts:

    {"type": "token", "text": "..."}
    {"type": "done", "stats": {...}}
"""

from __future__ import annotations

import json
import time
from typing import Iterator

from ..nethttp import HTTPError, open_url, stream_lines


class BackendError(Exception):
    pass


class Backend:
    name = "base"
    label = "Base"

    def status(self) -> dict:
        raise NotImplementedError

    def list_models(self) -> list[dict]:
        raise NotImplementedError

    def chat(self, model: str, messages: list[dict], options: dict | None = None) -> Iterator[dict]:
        raise NotImplementedError


def openai_chat_stream(url: str, model: str, messages: list[dict], options: dict | None, headers: dict | None = None,
                       timeout: float = 600) -> Iterator[dict]:
    """Stream an OpenAI-compatible /chat/completions endpoint (llama.cpp server, HF router)."""
    options = options or {}
    body = {"model": model, "messages": messages, "stream": True}
    for key in ("temperature", "top_p", "max_tokens", "seed"):
        if options.get(key) is not None:
            body[key] = options[key]
    started = time.time()
    first_token_at = None
    pieces = 0
    usage = None
    try:
        resp = open_url(url, "POST", body, headers, timeout=timeout)
    except HTTPError as exc:
        raise BackendError(exc.message if exc.status != 401 else "unauthorized: check your Hugging Face token") from None
    except OSError as exc:
        raise BackendError(f"couldn't reach {url}: {exc}") from None
    with resp:
        for line in stream_lines(resp):
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload == "[DONE]":
                break
            try:
                chunk = json.loads(payload)
            except ValueError:
                continue
            if chunk.get("error"):
                err = chunk["error"]
                raise BackendError(err.get("message") if isinstance(err, dict) else str(err))
            if chunk.get("usage"):
                usage = chunk["usage"]
            for choice in chunk.get("choices") or []:
                delta = choice.get("delta") or {}
                text = delta.get("content") or ""
                reasoning = delta.get("reasoning_content") or delta.get("reasoning") or ""
                if reasoning:
                    yield {"type": "reasoning", "text": reasoning}
                if text:
                    if first_token_at is None:
                        first_token_at = time.time()
                    pieces += 1
                    yield {"type": "token", "text": text}
    elapsed = time.time() - started
    tokens = (usage or {}).get("completion_tokens") or pieces
    gen_time = time.time() - (first_token_at or started)
    yield {
        "type": "done",
        "stats": {
            "tokens": tokens,
            "seconds": round(elapsed, 2),
            "first_token_seconds": round((first_token_at or time.time()) - started, 2),
            "tokens_per_second": round(tokens / gen_time, 1) if gen_time > 0 else None,
        },
    }
