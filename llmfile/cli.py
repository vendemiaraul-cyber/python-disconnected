"""Command line: llmfile serve | search | download | chat | info | pack | unpack | register."""

from __future__ import annotations

import argparse
import os
import sys

from . import hub
from .format import LLMFileError, new_metadata, open_llm, pack, unpack

DEFAULT_DIR = os.environ.get("LLMFILE_DIR") or os.path.join(os.path.expanduser("~"), "llm-files")


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit in ("B", "KB") else f"{n:.1f} {unit}"
        n /= 1024
    return str(n)


def cmd_serve(a):
    from .server import serve
    serve(a.dir, a.host, a.port, open_browser=not a.no_browser)


def cmd_search(a):
    results = hub.search(" ".join(a.query), limit=a.limit)
    if not results:
        print("No GGUF models found.")
    for m in results:
        print(f"{m['repo']:<60} {m['downloads']:>10,} downloads")


def cmd_files(a):
    for f in hub.list_files(a.repo):
        print(f"{human(f['size']):>9}  {f['file']}")


def cmd_download(a):
    files = hub.list_files(a.repo)
    if not files:
        sys.exit(f"{a.repo} has no single-file GGUF models.")
    if a.file:
        match = [f for f in files if f["file"] == a.file or a.file.upper() == f["quant"]]
        if not match:
            sys.exit(f"No file {a.file!r} in {a.repo}. Choose one of:\n  "
                     + "\n  ".join(f["file"] for f in files))
        choice = match[0]
    else:  # a sensible default: Q4_K_M if offered, else the smallest
        choice = next((f for f in files if f["quant"] == "Q4_K_M"), files[0])
    os.makedirs(a.dir, exist_ok=True)
    out = hub.unique_path(a.dir, a.output or hub.default_filename(choice["file"]))
    print(f"Downloading {a.repo}/{choice['file']} ({human(choice['size'])})")

    def progress(done, total):
        pct = f"{done / total * 100:5.1f}%" if total else ""
        print(f"\r  {pct} {human(done)} / {human(total)}   ", end="", flush=True)

    try:
        hub.download(a.repo, choice["file"], out, progress)
    except KeyboardInterrupt:
        sys.exit("\nCancelled.")
    print(f"\nSaved {out}\nChat with it:  llmfile chat \"{out}\"")


def cmd_chat(a):
    from .chat import repl
    repl(a.file, n_gpu_layers=a.gpu_layers)


def cmd_info(a):
    f = open_llm(a.file)
    src = f.meta.get("source", {})
    print(f"Name:          {f.name}")
    print(f"File:          {a.file}")
    print(f"Model size:    {human(f.model_size)}")
    if src.get("repo"):
        print(f"Source:        {src['repo']} / {src.get('file', '')}")
    print(f"Created:       {f.meta.get('created', '?')}")
    print(f"System prompt: {f.system_prompt or '(none)'}")
    print(f"Settings:      {', '.join(f'{k}={v}' for k, v in f.params.items())}")
    print(f"Saved chat:    {len(f.messages)} messages")
    if not f.has_trailer:
        print("Note: this is a plain GGUF without .llm metadata; it will be added on first chat.")


def cmd_set(a):
    f = open_llm(a.file)
    value = a.value
    if a.key == "name":
        f.meta["name"] = value
    elif a.key == "system_prompt":
        f.meta["system_prompt"] = value
    elif a.key in ("temperature", "n_ctx", "max_tokens"):
        num = float(value) if a.key == "temperature" else int(value)
        f.meta.setdefault("params", {})[a.key] = num
    f.save()
    print(f"Set {a.key} = {value}")


def cmd_pack(a):
    out = a.output or os.path.splitext(a.gguf)[0] + ".llm"
    name = a.name or os.path.splitext(os.path.basename(out))[0]
    pack(a.gguf, out, new_metadata(name, {"file": os.path.basename(a.gguf)}))
    print(f"Saved {out}")


def cmd_unpack(a):
    out = a.output or os.path.splitext(a.file)[0] + ".gguf"
    unpack(a.file, out)
    print(f"Saved {out}")


def cmd_register(a):
    from .register import register, unregister
    print(unregister() if a.remove else register())


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    # "llmfile example.llm" (what a double-click runs) means: chat with it.
    if len(argv) == 1 and argv[0].lower().endswith(".llm"):
        argv = ["chat", argv[0]]

    p = argparse.ArgumentParser(prog="llmfile", description="The .llm file type: "
                                "download a model as one file, then chat with it.")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("serve", help="open the website to search, download and chat")
    s.add_argument("--dir", default=DEFAULT_DIR, help=f"where .llm files live (default {DEFAULT_DIR})")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8765)
    s.add_argument("--no-browser", action="store_true", help="don't open a browser tab")
    s.set_defaults(fn=cmd_serve)

    s = sub.add_parser("search", help="search Hugging Face for downloadable models")
    s.add_argument("query", nargs="*")
    s.add_argument("--limit", type=int, default=20)
    s.set_defaults(fn=cmd_search)

    s = sub.add_parser("files", help="list the model files (sizes/quantizations) in a repo")
    s.add_argument("repo")
    s.set_defaults(fn=cmd_files)

    s = sub.add_parser("download", help="download a model as an .llm file")
    s.add_argument("repo", help="e.g. bartowski/SmolLM2-135M-Instruct-GGUF")
    s.add_argument("file", nargs="?", help="file name or quantization like Q4_K_M")
    s.add_argument("-o", "--output", help="file name, e.g. example.llm")
    s.add_argument("--dir", default=DEFAULT_DIR)
    s.set_defaults(fn=cmd_download)

    s = sub.add_parser("chat", help="chat with an .llm file in the terminal")
    s.add_argument("file")
    s.add_argument("--gpu-layers", type=int, default=0, help="layers to offload to the GPU (-1 = all)")
    s.set_defaults(fn=cmd_chat)

    s = sub.add_parser("info", help="show what's inside an .llm file")
    s.add_argument("file")
    s.set_defaults(fn=cmd_info)

    s = sub.add_parser("set", help="change a setting stored in an .llm file")
    s.add_argument("file")
    s.add_argument("key", choices=["name", "system_prompt", "temperature", "n_ctx", "max_tokens"])
    s.add_argument("value")
    s.set_defaults(fn=cmd_set)

    s = sub.add_parser("pack", help="turn a .gguf you already have into an .llm file")
    s.add_argument("gguf")
    s.add_argument("-o", "--output")
    s.add_argument("--name")
    s.set_defaults(fn=cmd_pack)

    s = sub.add_parser("unpack", help="extract the plain .gguf model from an .llm file")
    s.add_argument("file")
    s.add_argument("-o", "--output")
    s.set_defaults(fn=cmd_unpack)

    s = sub.add_parser("register", help="make double-clicking .llm files open a chat")
    s.add_argument("--remove", action="store_true", help="undo the registration")
    s.set_defaults(fn=cmd_register)

    a = p.parse_args(argv)
    try:
        a.fn(a)
    except (LLMFileError, OSError) as e:
        sys.exit(f"error: {e}")
