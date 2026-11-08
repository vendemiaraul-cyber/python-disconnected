"""llama.cpp's `llama-server` (OpenAI-compatible). Can also start one for a local GGUF file."""

from __future__ import annotations

import atexit
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Iterator
from urllib.parse import urlparse

from ..nethttp import get_json
from .base import Backend, BackendError, openai_chat_stream


class LlamaCppBackend(Backend):
    name = "llamacpp"
    label = "llama.cpp"

    def __init__(self, base_url: str, server_bin: str = "llama-server"):
        self.base_url = base_url.rstrip("/")
        self.server_bin = server_bin
        self._proc: subprocess.Popen | None = None
        self._loaded: str | None = None
        self._lock = threading.Lock()
        atexit.register(self.stop)

    def status(self) -> dict:
        try:
            get_json(f"{self.base_url}/health", timeout=3)
            detail = f"llama-server at {self.base_url}"
            if self._loaded:
                detail += f" running {Path(self._loaded).name}"
            return {"available": True, "detail": detail, "managed": self._proc is not None}
        except Exception:  # noqa: BLE001
            can_start = bool(shutil.which(self.server_bin) or Path(self.server_bin).is_file())
            hint = "Pick a downloaded GGUF and press Load to start one." if can_start else (
                "Install llama.cpp (llama-server) or start one yourself, e.g. `llama-server -m model.gguf`.")
            return {"available": False, "detail": f"No llama-server at {self.base_url}. {hint}", "can_start": can_start}

    def list_models(self) -> list[dict]:
        try:
            data = get_json(f"{self.base_url}/v1/models", timeout=5)
        except Exception as exc:  # noqa: BLE001
            raise BackendError(f"llama-server isn't reachable at {self.base_url}: {exc}") from None
        return [{"id": m["id"], "label": Path(m["id"]).name, "size": None, "details": "loaded"} for m in data.get("data", [])]

    def chat(self, model: str, messages: list[dict], options: dict | None = None) -> Iterator[dict]:
        return openai_chat_stream(f"{self.base_url}/v1/chat/completions", model, messages, options)

    def start(self, model_path: Path, context: int = 4096, extra_args: list[str] | None = None, wait: float = 180) -> dict:
        """Start (or restart) a managed llama-server for model_path and wait until it's healthy."""
        binary = shutil.which(self.server_bin) or (self.server_bin if Path(self.server_bin).is_file() else None)
        if not binary:
            raise BackendError("llama-server not found. Install llama.cpp or set LLAMA_SERVER=/path/to/llama-server.")
        parsed = urlparse(self.base_url)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or 8080
        with self._lock:
            self.stop()
            if self._port_in_use():
                raise BackendError(f"something else is already serving {self.base_url}; stop it or set LLAMACPP_URL")
            cmd = [binary, "-m", str(model_path), "--host", host, "--port", str(port), "-c", str(context)]
            cmd += extra_args or []
            self._proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
            self._loaded = str(model_path)
        deadline = time.time() + wait
        while time.time() < deadline:
            if self._proc.poll() is not None:
                err = (self._proc.stderr.read() if self._proc.stderr else "")[-800:]
                self._proc = None
                self._loaded = None
                raise BackendError(f"llama-server exited while loading: {err.strip() or 'no output'}")
            try:
                health = get_json(f"{self.base_url}/health", timeout=2)
                if health.get("status") in ("ok", "no slot available"):
                    return {"loaded": str(model_path), "url": self.base_url}
            except Exception:  # noqa: BLE001 - 503 while loading
                pass
            time.sleep(0.5)
        self.stop()
        raise BackendError("llama-server didn't become ready in time")

    def _port_in_use(self) -> bool:
        try:
            get_json(f"{self.base_url}/health", timeout=1)
            return True
        except Exception:  # noqa: BLE001
            return False

    def stop(self) -> None:
        proc, self._proc = self._proc, None
        self._loaded = None
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
