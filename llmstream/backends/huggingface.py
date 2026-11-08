"""Hugging Face Inference Providers through the OpenAI-compatible router.

Runs the big models (the ones that won't fit on your machine) remotely. Needs a
token from https://huggingface.co/settings/tokens in HF_TOKEN.
"""

from __future__ import annotations

from typing import Iterator

from ..nethttp import get_json
from .base import Backend, BackendError, openai_chat_stream


class HuggingFaceBackend(Backend):
    name = "huggingface"
    label = "Hugging Face"

    def __init__(self, router_url: str, token: str | None):
        self.router_url = router_url.rstrip("/")
        self.token = token
        self._cache: list[dict] | None = None

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}

    def status(self) -> dict:
        if not self.token:
            return {
                "available": True,
                "detail": "No HF_TOKEN set: you can browse models, but chatting needs a token from huggingface.co/settings/tokens.",
                "needs_token": True,
            }
        return {"available": True, "detail": "Hugging Face Inference Providers (token found)"}

    def list_models(self) -> list[dict]:
        if self._cache is None:
            try:
                data = get_json(f"{self.router_url}/models", headers=self._headers(), timeout=15)
            except Exception as exc:  # noqa: BLE001
                raise BackendError(f"couldn't list Hugging Face models: {exc}") from None
            models = []
            for m in data.get("data", []):
                providers = [p for p in m.get("providers", []) if p.get("status", "live") == "live"]
                ctx = max((p.get("context_length") or 0 for p in providers), default=0)
                free = any(p.get("is_free") for p in providers)
                details = f"{len(providers)} provider{'s' if len(providers) != 1 else ''}"
                if ctx:
                    details += f" · {ctx // 1000}k ctx"
                if free:
                    details += " · free tier"
                models.append({"id": m["id"], "label": m["id"], "size": None, "details": details})
            self._cache = models
        return self._cache

    def chat(self, model: str, messages: list[dict], options: dict | None = None) -> Iterator[dict]:
        if not self.token:
            raise BackendError("set HF_TOKEN (a Hugging Face access token) to chat with hosted models")
        return openai_chat_stream(f"{self.router_url}/chat/completions", model, messages, options, self._headers())
