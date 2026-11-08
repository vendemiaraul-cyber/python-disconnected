"""Ollama (https://ollama.com) via its local REST API."""

from __future__ import annotations

import json
import time
from typing import Iterator

from ..nethttp import HTTPError, get_json, open_url, stream_lines
from .base import Backend, BackendError


class OllamaBackend(Backend):
    name = "ollama"
    label = "Ollama"

    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")

    def status(self) -> dict:
        try:
            info = get_json(f"{self.base_url}/api/version", timeout=3)
            return {"available": True, "detail": f"Ollama {info.get('version', '')} at {self.base_url}"}
        except Exception:  # noqa: BLE001
            return {"available": False, "detail": f"Ollama isn't running at {self.base_url}. Start it with `ollama serve`."}

    def list_models(self) -> list[dict]:
        try:
            data = get_json(f"{self.base_url}/api/tags", timeout=5)
        except (HTTPError, OSError) as exc:
            raise BackendError(f"couldn't list Ollama models: {exc}") from None
        models = []
        for m in data.get("models", []):
            d = m.get("details") or {}
            models.append({
                "id": m["name"],
                "label": m["name"],
                "size": m.get("size"),
                "details": " · ".join(x for x in (d.get("parameter_size"), d.get("quantization_level"), d.get("family")) if x),
            })
        return models

    def installed(self, name: str) -> bool:
        try:
            names = {m["id"] for m in self.list_models()}
        except BackendError:
            return False
        if ":" not in name:
            name += ":latest"
        return name in names

    def chat(self, model: str, messages: list[dict], options: dict | None = None) -> Iterator[dict]:
        options = options or {}
        opts = {}
        if options.get("temperature") is not None:
            opts["temperature"] = options["temperature"]
        if options.get("top_p") is not None:
            opts["top_p"] = options["top_p"]
        if options.get("max_tokens"):
            opts["num_predict"] = options["max_tokens"]
        if options.get("num_ctx"):
            opts["num_ctx"] = options["num_ctx"]
        body = {"model": model, "messages": messages, "stream": True, "options": opts}
        started = time.time()
        first = None
        try:
            resp = open_url(f"{self.base_url}/api/chat", "POST", body, timeout=600)
        except HTTPError as exc:
            raise BackendError(exc.message) from None
        except OSError as exc:
            raise BackendError(f"couldn't reach Ollama at {self.base_url}: {exc}") from None
        with resp:
            for line in stream_lines(resp):
                chunk = json.loads(line)
                if chunk.get("error"):
                    raise BackendError(chunk["error"])
                msg = chunk.get("message") or {}
                if msg.get("thinking"):
                    yield {"type": "reasoning", "text": msg["thinking"]}
                if msg.get("content"):
                    if first is None:
                        first = time.time()
                    yield {"type": "token", "text": msg["content"]}
                if chunk.get("done"):
                    count = chunk.get("eval_count") or 0
                    dur = (chunk.get("eval_duration") or 0) / 1e9
                    yield {
                        "type": "done",
                        "stats": {
                            "tokens": count,
                            "seconds": round(time.time() - started, 2),
                            "first_token_seconds": round((first or time.time()) - started, 2),
                            "tokens_per_second": round(count / dur, 1) if dur else None,
                        },
                    }
                    return

    def pull(self, name: str) -> Iterator[dict]:
        """Stream `ollama pull` progress as {"status", "completed", "total"} dicts."""
        try:
            resp = open_url(f"{self.base_url}/api/pull", "POST", {"model": name, "stream": True}, timeout=3600)
        except HTTPError as exc:
            raise BackendError(exc.message) from None
        except OSError as exc:
            raise BackendError(f"couldn't reach Ollama at {self.base_url}: {exc}") from None
        with resp:
            for line in stream_lines(resp):
                chunk = json.loads(line)
                if chunk.get("error"):
                    raise BackendError(chunk["error"])
                yield chunk


def import_gguf_with_cli(name: str, gguf_path: str, timeout: float = 1800) -> str:
    """Register a local GGUF with Ollama via `ollama create` (needs the ollama CLI)."""
    import re
    import shutil
    import subprocess
    import tempfile

    if not re.fullmatch(r"[a-z0-9][a-z0-9._\-/]*(:[\w.\-]+)?", name):
        raise BackendError("model name may only use lowercase letters, digits, '.', '-', '_' and '/'")
    exe = shutil.which("ollama")
    if not exe:
        raise BackendError("the ollama command isn't installed")
    with tempfile.TemporaryDirectory() as tmp:
        modelfile = f"{tmp}/Modelfile"
        with open(modelfile, "w", encoding="utf-8") as fh:
            fh.write(f'FROM "{gguf_path}"\n')
        proc = subprocess.run([exe, "create", name, "-f", modelfile], capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise BackendError((proc.stderr or proc.stdout or "ollama create failed").strip()[-600:])
    return name
