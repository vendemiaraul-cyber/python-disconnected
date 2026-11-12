"""The .llm container format.

An .llm file is a GGUF model with a small JSON trailer appended to it:

    +------------------------------+
    | GGUF model bytes (unchanged) |  <- llama.cpp loads this directly
    +------------------------------+
    | JSON metadata (UTF-8)        |  name, source, settings, chat history
    +------------------------------+
    | uint64 LE  JSON length       |
    | uint64 LE  model length      |
    | 8 bytes    magic "LLMFILE1"  |
    +------------------------------+

GGUF readers locate tensors through offsets in the GGUF header, so they
ignore the trailing bytes, and an .llm file loads anywhere a .gguf does
without being extracted first. The trailer sits at the end, so saving a
chat rewrites only a few kilobytes, never the multi-gigabyte model.
"""

from __future__ import annotations

import json
import os
import struct
import time
from dataclasses import dataclass, field
from typing import Any

MAGIC = b"LLMFILE1"
GGUF_MAGIC = b"GGUF"
FOOTER = struct.Struct("<QQ8s")  # json_len, model_len, magic
FORMAT_VERSION = 1

DEFAULT_PARAMS = {"n_ctx": 4096, "temperature": 0.7, "max_tokens": 1024}


class LLMFileError(Exception):
    pass


@dataclass
class LLMFile:
    path: str
    model_size: int
    meta: dict[str, Any] = field(default_factory=dict)
    has_trailer: bool = True

    @property
    def name(self) -> str:
        return self.meta.get("name") or os.path.splitext(os.path.basename(self.path))[0]

    @property
    def messages(self) -> list[dict[str, str]]:
        return self.meta.setdefault("chat", [])

    @property
    def params(self) -> dict[str, Any]:
        return {**DEFAULT_PARAMS, **self.meta.get("params", {})}

    @property
    def system_prompt(self) -> str:
        return self.meta.get("system_prompt", "")

    def reset(self) -> None:
        self.messages.clear()
        self.save()

    def set_system_prompt(self, text: str) -> None:
        self.meta["system_prompt"] = text
        self.save()

    def save(self) -> None:
        write_trailer(self.path, self.model_size, self.meta)


def new_metadata(name: str, source: dict[str, Any] | None = None, **extra: Any) -> dict[str, Any]:
    return {
        "format": "llm",
        "version": FORMAT_VERSION,
        "name": name,
        "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source": source or {},
        "system_prompt": "You are a helpful assistant.",
        "params": dict(DEFAULT_PARAMS),
        "chat": [],
        **extra,
    }


def _read_footer(f) -> tuple[int, int] | None:
    f.seek(0, os.SEEK_END)
    size = f.tell()
    if size < FOOTER.size:
        return None
    f.seek(size - FOOTER.size)
    json_len, model_len, magic = FOOTER.unpack(f.read(FOOTER.size))
    if magic != MAGIC or model_len + json_len + FOOTER.size != size:
        return None
    return json_len, model_len


def open_llm(path: str) -> LLMFile:
    """Read an .llm file's metadata. A plain .gguf also opens, with empty metadata."""
    with open(path, "rb") as f:
        if f.read(4) != GGUF_MAGIC:
            raise LLMFileError(f"{path} is not an .llm file (no GGUF model inside)")
        footer = _read_footer(f)
        if footer is None:
            size = f.seek(0, os.SEEK_END)
            stem = os.path.splitext(os.path.basename(path))[0]
            return LLMFile(path, size, new_metadata(stem), has_trailer=False)
        json_len, model_len = footer
        f.seek(model_len)
        try:
            meta = json.loads(f.read(json_len).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            raise LLMFileError(f"{path}: metadata is corrupt ({e})") from e
    return LLMFile(path, model_len, meta)


def write_trailer(path: str, model_size: int, meta: dict[str, Any]) -> None:
    """Write (or rewrite) the metadata trailer after the model bytes.

    The file is never truncated: if the new metadata is shorter than the old,
    it is padded with spaces. That keeps saving safe on Windows while the
    model is memory-mapped, where shrinking a mapped file fails.
    """
    data = json.dumps(meta, ensure_ascii=False, indent=1).encode("utf-8")
    with open(path, "r+b") as f:
        old = _read_footer(f)
        if old is not None and old[1] == model_size and old[0] > len(data):
            data += b" " * (old[0] - len(data))
        f.seek(model_size)
        f.write(data)
        f.write(FOOTER.pack(len(data), model_size, MAGIC))
        f.flush()
        os.fsync(f.fileno())


def pack(gguf_path: str, out_path: str, meta: dict[str, Any]) -> LLMFile:
    """Copy a .gguf model into a new .llm file."""
    with open(gguf_path, "rb") as src, open(out_path, "xb") as dst:
        if src.read(4) != GGUF_MAGIC:
            raise LLMFileError(f"{gguf_path} is not a GGUF model")
        src.seek(0)
        while chunk := src.read(16 << 20):
            dst.write(chunk)
        model_size = dst.tell()
    write_trailer(out_path, model_size, meta)
    return LLMFile(out_path, model_size, meta)


def unpack(llm_path: str, out_path: str) -> None:
    """Write the bare GGUF model out of an .llm file."""
    llm = open_llm(llm_path)
    remaining = llm.model_size
    with open(llm_path, "rb") as src, open(out_path, "xb") as dst:
        while remaining:
            chunk = src.read(min(16 << 20, remaining))
            if not chunk:
                raise LLMFileError("unexpected end of file")
            dst.write(chunk)
            remaining -= len(chunk)
