"""Search Hugging Face for GGUF models and download them as .llm files."""

from __future__ import annotations

import json
import os
import re
import threading
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable

from .format import GGUF_MAGIC, LLMFileError, new_metadata, write_trailer

HF = os.environ.get("HF_ENDPOINT", "https://huggingface.co").rstrip("/")
USER_AGENT = "llmfile/0.1"
# Repos tagged with these (or untagged) can be chatted with; skip audio, embeddings, etc.
CHAT_PIPELINES = {None, "text-generation", "image-text-to-text", "text2text-generation"}
NON_CHAT_TAGS = {
    "text-to-speech", "automatic-speech-recognition", "audio-classification", "text-to-audio",
    "music-generation", "text-to-music", "voice-conversion", "text-to-image", "image-to-image",
    "text-to-video", "feature-extraction", "sentence-similarity", "sentence-transformers",
    "embeddings", "reranker", "text-classification", "token-classification", "diffusers",
}
SPLIT_RE = re.compile(r"-\d{5}-of-\d{5}\.gguf$", re.I)
QUANT_RE = re.compile(r"(IQ\d_[A-Z0-9_]+|Q\d_[A-Z0-9_]+|Q\d_\d|Q\d|F16|BF16|F32)", re.I)


def _request(url: str) -> urllib.request.Request:
    headers = {"User-Agent": USER_AGENT}
    token = os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return urllib.request.Request(url, headers=headers)


def _get_json(path: str, params: dict[str, Any] | None = None) -> Any:
    url = f"{HF}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params, doseq=True)
    with urllib.request.urlopen(_request(url), timeout=30) as r:
        return json.load(r)


def search(query: str, limit: int = 30) -> list[dict[str, Any]]:
    """Find models on Hugging Face that ship GGUF files, most downloaded first."""
    params = {"filter": "gguf", "sort": "downloads", "direction": "-1", "limit": limit * 2}
    if query.strip():
        params["search"] = query.strip()
    return [
        {
            "repo": m["id"],
            "downloads": m.get("downloads", 0),
            "likes": m.get("likes", 0),
            "updated": m.get("lastModified") or m.get("createdAt"),
            "tags": [t for t in m.get("tags", []) if ":" not in t][:8],
        }
        for m in _get_json("/api/models", params)
        if m.get("pipeline_tag") in CHAT_PIPELINES and not NON_CHAT_TAGS & set(m.get("tags", []))
    ][:limit]


def list_files(repo: str) -> list[dict[str, Any]]:
    """List the downloadable single-file GGUF models in a repo, smallest first."""
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", repo):
        raise LLMFileError(f"invalid repo name: {repo!r}")
    entries = _get_json(f"/api/models/{repo}/tree/main", {"recursive": "true"})
    files = []
    for e in entries:
        path = e.get("path", "")
        low = path.lower()
        if e.get("type") != "file" or not low.endswith(".gguf"):
            continue
        if "mmproj" in low or SPLIT_RE.search(path):
            continue  # vision projectors and multi-part models can't stand alone
        quant = QUANT_RE.search(os.path.basename(path))
        files.append({
            "file": path,
            "size": (e.get("lfs") or {}).get("size") or e.get("size", 0),
            "quant": quant.group(1).upper() if quant else "",
        })
    return sorted(files, key=lambda f: f["size"])


def default_filename(file: str) -> str:
    return os.path.splitext(os.path.basename(file))[0] + ".llm"


def unique_path(directory: str, filename: str) -> str:
    filename = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", filename).strip(" .") or "model"
    if not filename.lower().endswith(".llm"):
        filename += ".llm"
    stem, ext = os.path.splitext(filename)
    path, n = os.path.join(directory, filename), 2
    while os.path.exists(path) or os.path.exists(path + ".part"):
        path, n = os.path.join(directory, f"{stem}-{n}{ext}"), n + 1
    return path


def download(
    repo: str,
    file: str,
    out_path: str,
    progress: Callable[[int, int], None] | None = None,
    cancel: threading.Event | None = None,
) -> str:
    """Stream a GGUF from Hugging Face straight into a new .llm file.

    The model is written once, the metadata trailer is appended, and the
    finished file is moved into place. Nothing is copied twice.
    """
    url = f"{HF}/{repo}/resolve/main/{urllib.parse.quote(file)}"
    part = out_path + ".part"
    created = False
    try:
        with urllib.request.urlopen(_request(url), timeout=60) as r, open(part, "xb") as out:
            created = True
            total = int(r.headers.get("Content-Length") or 0)
            done = 0
            first = True
            while chunk := r.read(1 << 20):
                if cancel is not None and cancel.is_set():
                    raise LLMFileError("download cancelled")
                if first and not chunk.startswith(GGUF_MAGIC):
                    raise LLMFileError(f"{file} is not a GGUF model")
                first = False
                out.write(chunk)
                done += len(chunk)
                if progress:
                    progress(done, total)
        if total and done != total:
            raise LLMFileError(f"download incomplete ({done} of {total} bytes)")
        name = os.path.splitext(os.path.basename(out_path))[0]
        meta = new_metadata(name, {"repo": repo, "file": file, "url": f"{HF}/{repo}"})
        write_trailer(part, done, meta)
        os.replace(part, out_path)
        return out_path
    except urllib.error.HTTPError as e:
        if created and os.path.exists(part):
            os.remove(part)
        if e.code in (401, 403):
            raise LLMFileError(
                f"{repo} needs a Hugging Face login: accept its license on the website "
                "and set the HF_TOKEN environment variable") from e
        raise LLMFileError(f"download failed: HTTP {e.code}") from e
    except BaseException:
        if created and os.path.exists(part):
            os.remove(part)
        raise
