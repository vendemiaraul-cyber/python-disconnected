"""Work out what someone dropped or pasted: a Hugging Face repo or file, an Ollama
model, a direct download link, or a file already on disk."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlparse

HF_HOSTS = {"huggingface.co", "www.huggingface.co", "hf.co"}
OLLAMA_HOSTS = {"ollama.com", "www.ollama.com", "ollama.ai", "www.ollama.ai", "registry.ollama.ai"}

_REPO_PART = r"[A-Za-z0-9][\w.\-]*"
_HF_ID = re.compile(rf"^({_REPO_PART})/({_REPO_PART})(?::([\w.\-]+))?$")
_OLLAMA_ID = re.compile(r"^([a-z0-9][\w.\-]*)(?::([\w.\-]+))?$", re.IGNORECASE)
_URL_IN_TEXT = re.compile(r"https?://[^\s<>\"')\]]+")
_HF_SKIP_FIRST = {"datasets", "spaces", "docs", "blog", "models", "papers", "collections", "organizations", "settings", "join", "login"}


@dataclass
class Source:
    kind: str  # "hf" | "ollama" | "url" | "local"
    raw: str
    repo: str | None = None  # hf: "owner/name"
    revision: str = "main"
    filename: str | None = None  # hf: path inside repo; url: suggested file name
    quant: str | None = None  # hf: requested quant tag (e.g. from "repo:Q4_K_M")
    model: str | None = None  # ollama: "name" or "user/name"
    tag: str = "latest"  # ollama tag
    url: str | None = None  # direct url
    path: str | None = None  # local file

    def label(self) -> str:
        if self.kind == "hf":
            return self.repo + (f"/{self.filename}" if self.filename else "") + (f":{self.quant}" if self.quant else "")
        if self.kind == "ollama":
            return f"{self.model}:{self.tag}"
        if self.kind == "url":
            return self.url or self.raw
        return self.path or self.raw


class SourceError(ValueError):
    pass


def extract_candidate(text: str) -> str:
    """Pick the most useful line out of dropped text (a uri-list, a model card, a README...)."""
    text = (text or "").strip()
    if not text:
        raise SourceError("nothing to inspect")
    lines = [ln.strip() for ln in text.splitlines() if ln.strip() and not ln.strip().startswith("#")]
    if len(lines) == 1 and " " not in lines[0]:
        return lines[0]
    # Prefer a model-ish URL (HF / Ollama / .gguf), else any URL, else the first token-like line.
    urls = _URL_IN_TEXT.findall(text)
    for u in urls:
        host = urlparse(u).netloc.lower()
        if host in HF_HOSTS or host in OLLAMA_HOSTS or u.lower().split("?")[0].endswith(".gguf"):
            return u
    if urls:
        return urls[0]
    for ln in lines:
        if ln.lower().startswith("from "):  # an Ollama Modelfile
            return ln[5:].strip()
        if " " not in ln:
            return ln
    raise SourceError("couldn't find a model link or name in the dropped text")


def parse(text: str) -> Source:
    raw = extract_candidate(text)
    candidate = raw.strip().strip("<>").rstrip("/")

    # Local files (CLI use, or a path pasted into the box).
    expanded = Path(candidate).expanduser()
    if not candidate.startswith(("http://", "https://")) and expanded.suffix.lower() == ".gguf":
        return Source(kind="local", raw=raw, path=str(expanded))

    # Scheme shorthands.
    for prefix in ("hf://", "huggingface://"):
        if candidate.startswith(prefix):
            candidate = "https://huggingface.co/" + candidate[len(prefix):]
    if candidate.startswith("ollama://"):
        candidate = "ollama:" + candidate[len("ollama://"):]
    if candidate.startswith(("hf.co/", "huggingface.co/")):
        candidate = "https://" + candidate
    if candidate.startswith(("ollama.com/", "www.ollama.com/")):
        candidate = "https://" + candidate

    if candidate.startswith(("http://", "https://")):
        return _parse_url(raw, candidate)

    if candidate.lower().startswith("ollama:"):
        return _ollama(raw, candidate.split(":", 1)[1])

    m = _HF_ID.match(candidate)
    if m:
        return Source(kind="hf", raw=raw, repo=f"{m.group(1)}/{m.group(2)}", quant=_norm_quant(m.group(3)))

    if _OLLAMA_ID.match(candidate):
        return _ollama(raw, candidate)

    raise SourceError(f"don't know how to fetch {raw!r}; try a Hugging Face or Ollama link")


def _norm_quant(q: str | None) -> str | None:
    if not q or q.lower() == "latest":
        return None
    return q.upper()


def _ollama(raw: str, spec: str) -> Source:
    spec = spec.strip("/")
    if spec.startswith("library/"):
        spec = spec[len("library/"):]
    name, _, tag = spec.partition(":")
    if not name:
        raise SourceError("empty Ollama model name")
    return Source(kind="ollama", raw=raw, model=name.lower(), tag=tag or "latest")


def _parse_url(raw: str, url: str) -> Source:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    parts = [unquote(p) for p in parsed.path.split("/") if p]

    if host in HF_HOSTS:
        if not parts or parts[0] in _HF_SKIP_FIRST:
            if parts and parts[0] == "models" and len(parts) >= 3:
                parts = parts[1:]
            else:
                raise SourceError("that Hugging Face link isn't a model page")
        if len(parts) < 2:
            raise SourceError("that Hugging Face link doesn't name a model (expected huggingface.co/owner/model)")
        owner, name = parts[0], parts[1]
        quant = None
        if ":" in name:
            name, _, q = name.partition(":")
            quant = _norm_quant(q)
        src = Source(kind="hf", raw=raw, repo=f"{owner}/{name}", quant=quant)
        if len(parts) >= 4 and parts[2] in ("blob", "resolve", "tree"):
            src.revision = parts[3]
            if parts[2] != "tree" and len(parts) >= 5:
                src.filename = "/".join(parts[4:])
        return src

    if host in OLLAMA_HOSTS:
        if host == "registry.ollama.ai" and len(parts) >= 4 and parts[0] == "v2":
            # registry.ollama.ai/v2/library/name/manifests/tag
            ns, name = parts[1], parts[2]
            tag = parts[4] if len(parts) >= 5 else "latest"
            model = name if ns == "library" else f"{ns}/{name}"
            return Source(kind="ollama", raw=raw, model=model, tag=tag)
        if not parts:
            raise SourceError("that Ollama link doesn't name a model")
        if parts[0] == "library" and len(parts) >= 2:
            return _ollama(raw, parts[1])
        if parts[0] in ("search", "blog", "download", "docs", "models", "settings", "public"):
            raise SourceError("that Ollama link isn't a model page")
        if len(parts) >= 2 and parts[1] != "tags":
            return _ollama(raw, f"{parts[0]}/{parts[1]}")
        return _ollama(raw, parts[0])

    filename = parts[-1] if parts else "download.bin"
    return Source(kind="url", raw=raw, url=url, filename=filename)
