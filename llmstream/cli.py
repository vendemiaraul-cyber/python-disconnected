"""Command line: `llmstream` (web UI), `llmstream inspect`, `llmstream models`, `llmstream chat`."""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

from . import __version__
from .backends import BackendError
from .config import Settings
from .system import human_size

COLORS = {"ok": "\033[32m", "warn": "\033[33m", "bad": "\033[31m", "dim": "\033[2m", "bold": "\033[1m", "end": "\033[0m"}
ICONS = {"ok": "✓", "warn": "!", "bad": "✗"}


def _c(name: str, text: str) -> str:
    if not sys.stdout.isatty() or os.environ.get("NO_COLOR"):
        return text
    return f"{COLORS[name]}{text}{COLORS['end']}"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="llmstream", description="Stream chat from WebLLM, Ollama, llama.cpp and Hugging Face.")
    p.add_argument("--version", action="version", version=f"llmstream {__version__}")
    p.add_argument("--models-dir", help="where downloaded GGUF files go (default ~/.llmstream/models)")
    p.add_argument("--ollama-url", help="Ollama server (default $OLLAMA_HOST or http://127.0.0.1:11434)")
    p.add_argument("--llamacpp-url", help="llama-server URL (default http://127.0.0.1:8080)")
    sub = p.add_subparsers(dest="command")

    s = sub.add_parser("serve", help="start the web UI (the default)")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8765)
    s.add_argument("--no-browser", action="store_true", help="don't open a browser tab")

    i = sub.add_parser("inspect", help="check whether a model can be downloaded and run here")
    i.add_argument("model", help="Hugging Face / Ollama link or id, a .gguf URL, or a local .gguf path")
    i.add_argument("--json", action="store_true", help="print the raw report")

    m = sub.add_parser("models", help="list models")
    m.add_argument("backend", nargs="?", default="all", choices=["all", "ollama", "llamacpp", "huggingface", "local"])

    c = sub.add_parser("chat", help="chat in the terminal")
    c.add_argument("--backend", "-b", default="ollama", choices=["ollama", "llamacpp", "huggingface"])
    c.add_argument("--model", "-m", required=True)
    c.add_argument("--system", "-s", help="system prompt")
    c.add_argument("--temperature", "-t", type=float)
    c.add_argument("prompt", nargs="*", help="one-shot prompt; omit for an interactive chat")
    return p


def make_settings(args) -> Settings:
    if args.models_dir:
        os.environ["LLMSTREAM_MODELS"] = str(Path(args.models_dir).expanduser())
    s = Settings()
    if args.ollama_url:
        s.ollama_url = args.ollama_url.rstrip("/")
    if args.llamacpp_url:
        s.llamacpp_url = args.llamacpp_url.rstrip("/")
    return s


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = make_settings(args)
    cmd = args.command or "serve"
    try:
        if cmd == "serve":
            from .server import serve

            serve(settings, getattr(args, "host", "127.0.0.1"), getattr(args, "port", 8765),
                  open_browser=not getattr(args, "no_browser", False))
            return 0
        if cmd == "inspect":
            return cmd_inspect(settings, args)
        if cmd == "models":
            return cmd_models(settings, args.backend)
        if cmd == "chat":
            return cmd_chat(settings, args)
    except KeyboardInterrupt:
        print()
        return 130
    return 0


# ---------------------------------------------------------------- inspect

def cmd_inspect(settings: Settings, args) -> int:
    import json

    from .backends import OllamaBackend
    from .downloads import Downloader
    from .probe import Inspector, PlanStore
    from .sources import SourceError

    ollama = OllamaBackend(settings.ollama_url)
    plans = PlanStore()
    try:
        report = Inspector(settings, plans, ollama).inspect(args.model)
    except SourceError as exc:
        print(_c("bad", f"✗ {exc}"))
        return 2
    if args.json:
        print(json.dumps(report, indent=2))
        return 0
    print_report(report)

    choices = [(opt, act) for opt in report["options"] for act in opt["actions"] if act["kind"] in ("download", "ollama-pull")]
    if not choices:
        for opt in report["options"]:
            if opt.get("local_path"):
                print(f"\nRun it with:  llama-server -m \"{opt['local_path']}\"")
        return 0 if report["access"]["ok"] else 1

    rec = next((n for n, (o, _) in enumerate(choices) if o.get("recommended")), 0)
    print()
    for n, (opt, act) in enumerate(choices, 1):
        mark = _c("bold", " ← recommended") if n - 1 == rec else ""
        lvl = opt["fit"]["level"]
        print(f"  {n:>2}. {_c(lvl, ICONS[lvl])} {opt['title']:<10} {opt['size_text']:>9}  {act['label']}{mark}")
    try:
        raw = input(f"\nWhich one? [1-{len(choices)}, Enter = {rec + 1}, q = quit] ").strip().lower()
    except EOFError:
        return 0
    if raw in ("q", "quit", "n", "no"):
        return 0
    try:
        idx = int(raw) - 1 if raw else rec
        opt, act = choices[idx]
    except (ValueError, IndexError):
        print("never mind")
        return 1

    where = act.get("dest") or f"Ollama as {act.get('ollama_model')}"
    warn = ""
    if opt["fit"]["level"] != "ok":
        warn = _c(opt["fit"]["level"], f"\n  Heads up: {opt['fit']['messages'][-1]}")
    print(f"\nThis will download {_c('bold', opt['size_text'])} to {where}.{warn}")
    try:
        ok = input("Go ahead? [y/N] ").strip().lower() in ("y", "yes")
    except EOFError:
        ok = False
    if not ok:
        print("Nothing downloaded.")
        return 0

    downloader = Downloader(settings, ollama)
    job = downloader.start(plans.get(act["plan_id"]))
    try:
        for snap in job.events():
            if snap.get("heartbeat"):
                continue
            _progress(snap)
    except KeyboardInterrupt:
        downloader.cancel(job.id)
        time.sleep(0.5)
        print("\nStopped. Run the same command again to resume.")
        return 130
    print()
    if job.state == "done":
        res = job.result
        print(_c("ok", "✓ Done."), end=" ")
        if res.get("path"):
            print(f"Saved to {res['path']}\n  Chat with it:  llama-server -m \"{res['path']}\"   (or load it from the web UI)")
        else:
            print(f"Chat with it:  llmstream chat -b ollama -m {res.get('ollama_model')}")
        return 0
    print(_c("bad", f"✗ {job.error or job.status}"))
    return 1


def _progress(snap: dict):
    total, done = snap.get("total") or 0, snap.get("completed") or 0
    width = 28
    if total:
        filled = int(width * min(done / total, 1))
        bar = "█" * filled + "░" * (width - filled)
        pct = f"{100 * done / total:5.1f}%"
    else:
        bar, pct = "░" * width, "  ?  "
    speed = f"{human_size(snap['speed'])}/s" if snap.get("speed") else ""
    line = f"\r{bar} {pct} {human_size(done)} / {human_size(total) if total else '?'} {speed}  {snap.get('status', '')[:40]}"
    sys.stdout.write(line.ljust(110))
    sys.stdout.flush()


def print_report(r: dict):
    v = r["verdict"]
    print(_c("bold", r["title"]) + _c("dim", f"  ({r['source']['kind']})"))
    if r["facts"]:
        print(_c("dim", "  " + " · ".join(r["facts"])))
    print(f"\n{_c(v['level'], ICONS[v['level']])} {v['headline']}")
    acc = r["access"]
    if acc.get("message"):
        print(f"  access: {_c('ok' if acc['ok'] else 'bad', acc['message'])}")
    sysi = r["system"]
    print(_c("dim", f"  this machine: {human_size(sysi['memory_total'])} RAM ({human_size(sysi['memory_available'])} free), "
                    f"{human_size(sysi['disk_free'])} free disk" + (f", {sysi['gpu']}" if sysi.get("gpu") else "")))
    rec = next((o for o in r["options"] if o.get("recommended")), None)
    if rec and rec.get("fit"):
        for msg in rec["fit"]["messages"]:
            print(f"  {_c(rec['fit']['level'], '•')} {msg}")
    for note in r["notes"]:
        print(f"  • {note}")
    if r["suggestions"]:
        print("\n  GGUF versions you can inspect:")
        for s in r["suggestions"]:
            print(f"    llmstream inspect {s['input']}   {_c('dim', s.get('detail', ''))}")
    remote = [a for o in r["options"] for a in o["actions"] if a["kind"] == "remote"]
    if remote:
        print(f"\n  Or chat remotely:  llmstream chat -b huggingface -m {remote[0]['remote_model']}")


# ---------------------------------------------------------------- models / chat

def _backends(settings: Settings) -> dict:
    from .backends import HuggingFaceBackend, LlamaCppBackend, OllamaBackend

    return {
        "ollama": OllamaBackend(settings.ollama_url),
        "llamacpp": LlamaCppBackend(settings.llamacpp_url, settings.llama_server_bin),
        "huggingface": HuggingFaceBackend(settings.hf_router_url, settings.hf_token),
    }


def cmd_models(settings: Settings, which: str) -> int:
    from .downloads import local_models

    names = ["local", "ollama", "llamacpp", "huggingface"] if which == "all" else [which]
    backends = _backends(settings)
    for name in names:
        print(_c("bold", {"local": "Downloaded GGUF files", "ollama": "Ollama", "llamacpp": "llama.cpp server",
                          "huggingface": "Hugging Face Inference"}[name]))
        try:
            models = local_models(settings.models_dir) if name == "local" else backends[name].list_models()
        except BackendError as exc:
            print(_c("dim", f"  {exc}\n"))
            continue
        if not models:
            print(_c("dim", "  (none)"))
        for m in models[:60]:
            size = human_size(m["size"]) if m.get("size") else ""
            print(f"  {m['label']:<48} {size:>9}  {_c('dim', m.get('details') or '')}")
        if len(models) > 60:
            print(_c("dim", f"  … and {len(models) - 60} more"))
        print()
    return 0


def cmd_chat(settings: Settings, args) -> int:
    backend = _backends(settings)[args.backend]
    messages: list[dict] = []
    if args.system:
        messages.append({"role": "system", "content": args.system})
    options = {"temperature": args.temperature}
    one_shot = " ".join(args.prompt).strip()

    def turn(text: str) -> bool:
        messages.append({"role": "user", "content": text})
        reply = []
        try:
            for ev in backend.chat(args.model, messages, options):
                if ev["type"] == "token":
                    reply.append(ev["text"])
                    sys.stdout.write(ev["text"])
                    sys.stdout.flush()
                elif ev["type"] == "reasoning":
                    sys.stdout.write(_c("dim", ev["text"]))
                    sys.stdout.flush()
                elif ev["type"] == "done":
                    st = ev["stats"]
                    tps = f" · {st['tokens_per_second']} tok/s" if st.get("tokens_per_second") else ""
                    print(_c("dim", f"\n[{st['tokens']} tokens · {st['seconds']}s{tps}]"))
        except BackendError as exc:
            print(_c("bad", f"\n✗ {exc}"))
            messages.pop()
            return False
        except KeyboardInterrupt:
            print(_c("dim", "\n[stopped]"))
        messages.append({"role": "assistant", "content": "".join(reply)})
        return True

    if one_shot:
        return 0 if turn(one_shot) else 1
    print(_c("dim", f"Chatting with {args.model} on {backend.label}. Ctrl+D to quit, /reset to start over."))
    while True:
        try:
            text = input(_c("bold", "\nyou › ")).strip()
        except EOFError:
            print()
            return 0
        if not text:
            continue
        if text in ("/reset", "/clear"):
            messages[:] = messages[:1] if args.system else []
            print(_c("dim", "(new conversation)"))
            continue
        if text in ("/quit", "/exit"):
            return 0
        turn(text)
