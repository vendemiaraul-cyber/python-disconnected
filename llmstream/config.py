"""Runtime settings, resolved from CLI flags, then environment variables, then defaults."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _hf_token_from_disk() -> str | None:
    home = os.environ.get("HF_HOME") or os.path.join(Path.home(), ".cache", "huggingface")
    path = Path(home) / "token"
    try:
        token = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return token or None


def _ollama_url() -> str:
    host = os.environ.get("OLLAMA_HOST", "").strip()
    if not host:
        return "http://127.0.0.1:11434"
    if not host.startswith(("http://", "https://")):
        host = "http://" + host
    # OLLAMA_HOST=0.0.0.0 is a listen address; connect to loopback instead.
    host = host.replace("://0.0.0.0", "://127.0.0.1")
    if host.count(":") == 1:
        host += ":11434"
    return host.rstrip("/")


@dataclass
class Settings:
    home: Path = field(default_factory=lambda: Path(os.environ.get("LLMSTREAM_HOME", Path.home() / ".llmstream")))
    ollama_url: str = field(default_factory=_ollama_url)
    llamacpp_url: str = field(default_factory=lambda: os.environ.get("LLAMACPP_URL", "http://127.0.0.1:8080").rstrip("/"))
    llama_server_bin: str = field(default_factory=lambda: os.environ.get("LLAMA_SERVER", "llama-server"))
    hf_token: str | None = field(
        default_factory=lambda: os.environ.get("HF_TOKEN")
        or os.environ.get("HUGGING_FACE_HUB_TOKEN")
        or _hf_token_from_disk()
    )
    hf_router_url: str = field(default_factory=lambda: os.environ.get("HF_ROUTER_URL", "https://router.huggingface.co/v1").rstrip("/"))
    hf_hub_url: str = field(default_factory=lambda: os.environ.get("HF_ENDPOINT", "https://huggingface.co").rstrip("/"))
    ollama_registry_url: str = "https://registry.ollama.ai"

    @property
    def models_dir(self) -> Path:
        path = Path(os.environ.get("LLMSTREAM_MODELS", self.home / "models"))
        path.mkdir(parents=True, exist_ok=True)
        return path
