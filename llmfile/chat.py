"""Run an .llm file with llama.cpp and keep the conversation inside the file."""

from __future__ import annotations

import os
import sys
import threading
from typing import Iterator

from .format import LLMFile, open_llm


def _load_llama():
    try:
        from llama_cpp import Llama
    except ImportError:
        sys.exit("Chatting needs llama-cpp-python:  pip install llama-cpp-python")
    return Llama


class ChatSession:
    """A loaded .llm model whose chat history lives in the file itself."""

    def __init__(self, path: str, n_ctx: int | None = None, n_gpu_layers: int = 0):
        self.file: LLMFile = open_llm(path)
        Llama = _load_llama()
        self.n_ctx = n_ctx or int(self.file.params["n_ctx"])
        self.llm = Llama(
            model_path=path,
            n_ctx=self.n_ctx,
            n_gpu_layers=n_gpu_layers,
            verbose=False,
        )
        self.lock = threading.Lock()

    @property
    def path(self) -> str:
        return self.file.path

    def _prompt_messages(self, reserve: int) -> list[dict[str, str]]:
        """System prompt plus as much recent history as fits in the context window."""
        system = self.file.system_prompt
        budget = self.n_ctx - reserve - (self._tokens(system) + 8 if system else 0)
        kept: list[dict[str, str]] = []
        for m in reversed(self.file.messages):
            budget -= self._tokens(m["content"]) + 8
            if budget < 0 and kept:
                break
            kept.append(m)
        kept.reverse()
        return ([{"role": "system", "content": system}] if system else []) + kept

    def _tokens(self, text: str) -> int:
        return len(self.llm.tokenize(text.encode("utf-8"), add_bos=False, special=False))

    def send(self, text: str) -> Iterator[str]:
        """Add a user message, stream the reply, then save both into the file."""
        with self.lock:
            params = self.file.params
            max_tokens = min(int(params["max_tokens"]), self.n_ctx // 2)
            self.file.messages.append({"role": "user", "content": text})
            reply = []
            try:
                stream = self.llm.create_chat_completion(
                    messages=self._prompt_messages(max_tokens),
                    max_tokens=max_tokens,
                    temperature=float(params["temperature"]),
                    stream=True,
                )
                for chunk in stream:
                    piece = chunk["choices"][0]["delta"].get("content")
                    if piece:
                        reply.append(piece)
                        yield piece
            finally:
                # Saved even if interrupted, so a half reply isn't lost.
                self.file.messages.append({"role": "assistant", "content": "".join(reply)})
                self.file.save()

    def reset(self) -> None:
        with self.lock:
            self.file.messages.clear()
            self.file.save()

    def set_system_prompt(self, text: str) -> None:
        with self.lock:
            self.file.meta["system_prompt"] = text
            self.file.save()


HELP = """Commands:
  /reset           forget this conversation
  /system <text>   change the system prompt (no text: show it)
  /history         print the whole conversation
  /exit            quit (the chat is already saved in the file)"""


def repl(path: str, n_gpu_layers: int = 0) -> None:
    print(f"Loading {os.path.basename(path)} ...", flush=True)
    session = ChatSession(path, n_gpu_layers=n_gpu_layers)
    f = session.file
    src = f.meta.get("source", {}).get("repo")
    print(f"\n{f.name}" + (f"  (from {src})" if src else ""))
    print("Type a message, or /help for commands. Ctrl+C or /exit to quit.\n")
    if f.messages:
        print(f"-- resuming conversation ({len(f.messages)} messages saved in this file) --")
        for m in f.messages[-4:]:
            who = "you" if m["role"] == "user" else "ai "
            print(f"{who}> {m['content']}\n")
    while True:
        try:
            text = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not text:
            continue
        if text.startswith("/"):
            cmd, _, arg = text.partition(" ")
            if cmd in ("/exit", "/quit"):
                return
            elif cmd == "/reset":
                session.reset()
                print("Conversation cleared.\n")
            elif cmd == "/system":
                if arg:
                    session.set_system_prompt(arg)
                    print("System prompt saved.\n")
                else:
                    print(f"{f.system_prompt or '(none)'}\n")
            elif cmd == "/history":
                for m in f.messages:
                    print(f"{'you' if m['role'] == 'user' else 'ai '}> {m['content']}\n")
            else:
                print(HELP + "\n")
            continue
        print("ai > ", end="", flush=True)
        reply = session.send(text)
        try:
            for piece in reply:
                print(piece, end="", flush=True)
        except KeyboardInterrupt:
            reply.close()  # saves the partial reply
            print(" [stopped]", end="")
        print("\n")
