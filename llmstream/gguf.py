"""Read the metadata header of a GGUF model file without loading the weights.

Works on a truncated prefix too (the browser only uploads the first few MB of a
dropped file), returning whatever keys were complete.
"""

from __future__ import annotations

import re
import struct
from pathlib import Path

MAGIC = b"GGUF"

FILE_TYPES = {
    0: "F32", 1: "F16", 2: "Q4_0", 3: "Q4_1", 7: "Q8_0", 8: "Q5_0", 9: "Q5_1",
    10: "Q2_K", 11: "Q3_K_S", 12: "Q3_K_M", 13: "Q3_K_L", 14: "Q4_K_S", 15: "Q4_K_M",
    16: "Q5_K_S", 17: "Q5_K_M", 18: "Q6_K", 19: "IQ2_XXS", 20: "IQ2_XS", 21: "Q2_K_S",
    22: "IQ3_XS", 23: "IQ3_XXS", 24: "IQ1_S", 25: "IQ4_NL", 26: "IQ3_S", 27: "IQ3_M",
    28: "IQ2_S", 29: "IQ2_M", 30: "IQ4_XS", 31: "IQ1_M", 32: "BF16", 36: "TQ1_0", 37: "TQ2_0",
}

_SCALARS = {
    0: "<B", 1: "<b", 2: "<H", 3: "<h", 4: "<I", 5: "<i", 6: "<f", 7: "<?", 10: "<Q", 11: "<q", 12: "<d",
}
_STRING, _ARRAY = 8, 9
_KEEP_ARRAY_ITEMS = 16

QUANT_RE = re.compile(
    r"(?<![A-Za-z0-9])(I?Q\d(?:_[0-9A-Z]+)*|TQ\d_\d|BF16|F16|FP16|F32|MXFP4)(?![A-Za-z0-9])",
    re.IGNORECASE,
)


class GGUFError(ValueError):
    pass


class _Truncated(Exception):
    pass


class _Reader:
    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0

    def take(self, n: int) -> bytes:
        end = self.pos + n
        if end > len(self.data):
            raise _Truncated
        chunk = self.data[self.pos:end]
        self.pos = end
        return chunk

    def unpack(self, fmt: str):
        return struct.unpack(fmt, self.take(struct.calcsize(fmt)))[0]

    def string(self, len_fmt: str) -> str:
        n = self.unpack(len_fmt)
        if n > 1 << 30:
            raise GGUFError("corrupt string length")
        return self.take(n).decode("utf-8", "replace")

    def value(self, vtype: int, len_fmt: str):
        if vtype in _SCALARS:
            return self.unpack(_SCALARS[vtype])
        if vtype == _STRING:
            return self.string(len_fmt)
        if vtype == _ARRAY:
            itype = self.unpack("<I")
            count = self.unpack(len_fmt)
            items = []
            for i in range(count):
                item = self.value(itype, len_fmt)
                if i < _KEEP_ARRAY_ITEMS:
                    items.append(item)
            return {"array_length": count, "items": items} if count > _KEEP_ARRAY_ITEMS else items
        raise GGUFError(f"unknown GGUF value type {vtype}")


def parse_header(data: bytes) -> dict:
    """Parse GGUF metadata from the first bytes of a file.

    Returns {"version", "tensor_count", "kv_count", "metadata": {...}, "truncated": bool}.
    """
    if data[:4] != MAGIC:
        raise GGUFError("not a GGUF file (missing 'GGUF' magic bytes)")
    r = _Reader(data)
    r.take(4)
    version = r.unpack("<I")
    if version == 1:
        len_fmt = "<I"
    elif version in (2, 3):
        len_fmt = "<Q"
    else:
        raise GGUFError(f"unsupported GGUF version {version}")
    tensor_count = r.unpack(len_fmt)
    kv_count = r.unpack(len_fmt)
    metadata: dict = {}
    truncated = False
    try:
        for _ in range(kv_count):
            key = r.string(len_fmt)
            vtype = r.unpack("<I")
            metadata[key] = r.value(vtype, len_fmt)
    except _Truncated:
        truncated = True
    return {
        "version": version,
        "tensor_count": tensor_count,
        "kv_count": kv_count,
        "metadata": metadata,
        "truncated": truncated,
    }


def read_file_header(path: Path, max_bytes: int = 32 * 1024 * 1024) -> dict:
    with open(path, "rb") as fh:
        return parse_header(fh.read(max_bytes))


def quant_from_name(filename: str) -> str | None:
    matches = QUANT_RE.findall(Path(filename).name)
    return matches[-1].upper() if matches else None


def summarize(header: dict, file_size: int | None = None, filename: str | None = None) -> dict:
    """Pull out the fields a person cares about when deciding whether a model will run."""
    md = header.get("metadata", {})
    arch = md.get("general.architecture")

    def a(key):
        return md.get(f"{arch}.{key}") if arch else None

    ftype = md.get("general.file_type")
    quant = FILE_TYPES.get(ftype) if isinstance(ftype, int) else None
    if not quant and filename:
        quant = quant_from_name(filename)
    n_embd, n_head, n_head_kv = a("embedding_length"), a("attention.head_count"), a("attention.head_count_kv")
    if isinstance(n_head, list):  # some archs store per-layer head counts
        n_head = max(n_head) if n_head else None
    if isinstance(n_head_kv, list):
        n_head_kv = max(n_head_kv) if n_head_kv else None
    return {
        "name": md.get("general.name") or (Path(filename).stem if filename else None),
        "architecture": arch,
        "size_label": md.get("general.size_label"),
        "parameters": md.get("general.parameter_count"),
        "quantization": quant,
        "context_length": a("context_length"),
        "layers": a("block_count"),
        "embedding_length": n_embd,
        "head_count": n_head,
        "head_count_kv": n_head_kv or n_head,
        "has_chat_template": "tokenizer.chat_template" in md,
        "license": md.get("general.license"),
        "tensor_count": header.get("tensor_count"),
        "file_size": file_size,
        "split_count": md.get("split.count"),
        "header_truncated": header.get("truncated", False),
    }


def kv_cache_bytes(summary: dict, context: int = 4096, bytes_per_value: int = 2) -> int:
    """Rough f16 KV-cache size for a given context length (0 when the header lacks the shape)."""
    layers, n_embd = summary.get("layers"), summary.get("embedding_length")
    n_head, n_kv = summary.get("head_count"), summary.get("head_count_kv")
    if not all(isinstance(x, int) and x > 0 for x in (layers, n_embd, n_head, n_kv)):
        return 0
    head_dim = n_embd // n_head
    return 2 * layers * context * n_kv * head_dim * bytes_per_value
