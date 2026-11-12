"""The local website: search models, download them as .llm files, and chat."""

from __future__ import annotations

import json
import os
import threading
import traceback
import urllib.parse
import uuid
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources

from . import hub
from .format import LLMFileError, open_llm


class App:
    def __init__(self, library: str, local_only: bool = True):
        self.local_only = local_only
        self.library = os.path.abspath(library)
        os.makedirs(self.library, exist_ok=True)
        self.jobs: dict[str, dict] = {}
        self.cancels: dict[str, threading.Event] = {}
        self.session = None  # one loaded model at a time
        self.session_lock = threading.Lock()

    # -- files -------------------------------------------------------------
    def resolve(self, name: str) -> str:
        """Map a file name from the browser to a path inside the library only."""
        base = os.path.basename(name or "")
        if not base.lower().endswith(".llm") or base != name:
            raise LLMFileError(f"not an .llm file name: {name!r}")
        path = os.path.join(self.library, base)
        if not os.path.isfile(path):
            raise LLMFileError(f"{base} not found")
        return path

    def library_list(self) -> list[dict]:
        out = []
        for entry in sorted(os.scandir(self.library), key=lambda e: e.name.lower()):
            if not (entry.is_file() and entry.name.lower().endswith(".llm")):
                continue
            try:
                f = open_llm(entry.path)
            except (OSError, LLMFileError) as e:
                out.append({"file": entry.name, "error": str(e)})
                continue
            out.append({
                "file": entry.name,
                "name": f.name,
                "size": entry.stat().st_size,
                "source": f.meta.get("source", {}),
                "messages": len(f.messages),
                "created": f.meta.get("created"),
            })
        return out

    # -- downloads ---------------------------------------------------------
    def start_download(self, repo: str, file: str, filename: str | None) -> dict:
        out = hub.unique_path(self.library, filename or hub.default_filename(file))
        job_id = uuid.uuid4().hex[:12]
        job = {"id": job_id, "repo": repo, "file": file, "out": os.path.basename(out),
               "done": 0, "total": 0, "status": "downloading", "error": None}
        cancel = threading.Event()
        self.jobs[job_id], self.cancels[job_id] = job, cancel

        def progress(done, total):
            job["done"], job["total"] = done, total

        def run():
            try:
                hub.download(repo, file, out, progress, cancel)
                job["status"] = "done"
            except Exception as e:  # reported to the page
                job["status"] = "cancelled" if cancel.is_set() else "error"
                job["error"] = str(e)

        threading.Thread(target=run, daemon=True).start()
        return job

    def edit_file(self, path: str, change) -> None:
        """Change an .llm file's metadata, through the loaded model if it's this one."""
        with self.session_lock:
            session = self.session if self.session and self.session.path == path else None
        if session:
            change(session)
            return
        f = open_llm(path)
        change(f)
        f.save()

    # -- chat --------------------------------------------------------------
    def get_session(self, path: str):
        from .chat import ChatSession
        with self.session_lock:
            if self.session is None or self.session.path != path:
                self.session = None  # free the old model first
                self.session = ChatSession(path)
            return self.session


def make_handler(app: App):
    class Handler(BaseHTTPRequestHandler):
        server_version = "llmfile"

        def log_message(self, fmt, *args):
            pass

        def send_json(self, data, status=200):
            body = json.dumps(data).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def body(self) -> dict:
            n = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(n) or b"{}")

        def do_GET(self):
            url = urllib.parse.urlparse(self.path)
            q = {k: v[0] for k, v in urllib.parse.parse_qs(url.query).items()}
            self.handle_api(url.path, q, None)

        def do_POST(self):
            # Only accept requests from our own page, so other websites
            # open in the browser can't drive this server.
            if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                return self.send_json({"error": "expected JSON"}, 415)
            url = urllib.parse.urlparse(self.path)
            self.handle_api(url.path, {}, self.body())

        def handle_api(self, path, q, body):
            host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]")
            if app.local_only and host not in ("localhost", "127.0.0.1", "::1"):
                return self.send_json({"error": "forbidden host"}, 403)  # DNS rebinding
            try:
                self.route(path, q, body)
            except LLMFileError as e:
                self.send_json({"error": str(e)}, 400)
            except Exception as e:
                traceback.print_exc()
                self.send_json({"error": f"{type(e).__name__}: {e}"}, 500)

        def route(self, path, q, body):
            if path == "/" and body is None:
                html = resources.files("llmfile").joinpath("static/index.html").read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(html)))
                self.end_headers()
                self.wfile.write(html)
            elif path == "/api/search":
                self.send_json(hub.search(q.get("q", "")))
            elif path == "/api/files":
                self.send_json(hub.list_files(q.get("repo", "")))
            elif path == "/api/library":
                self.send_json({"dir": app.library, "files": app.library_list()})
            elif path == "/api/jobs":
                self.send_json(list(app.jobs.values()))
            elif path == "/api/history":
                f = open_llm(app.resolve(q.get("file", "")))
                self.send_json({"name": f.name, "messages": f.messages,
                                "system_prompt": f.system_prompt,
                                "source": f.meta.get("source", {})})
            elif path == "/api/download" and body is not None:
                self.send_json(app.start_download(body["repo"], body["file"], body.get("filename")))
            elif path == "/api/cancel" and body is not None:
                if ev := app.cancels.get(body.get("id", "")):
                    ev.set()
                self.send_json({"ok": True})
            elif path == "/api/system" and body is not None:
                text = body.get("text", "")
                app.edit_file(app.resolve(body["file"]), lambda t: t.set_system_prompt(text))
                self.send_json({"ok": True})
            elif path == "/api/reset" and body is not None:
                app.edit_file(app.resolve(body["file"]), lambda t: t.reset())
                self.send_json({"ok": True})
            elif path == "/api/chat" and body is not None:
                self.stream_chat(app.resolve(body["file"]), body["message"])
            else:
                self.send_json({"error": "not found"}, 404)

        def stream_chat(self, path, message):
            session = app.get_session(path)
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            reply = session.send(message)
            try:
                for piece in reply:
                    self.wfile.write(piece.encode("utf-8"))
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass  # the page hit Stop; the partial reply is still saved
            except Exception as e:
                traceback.print_exc()
                self.wfile.write(f"\n[error: {e}]".encode())
            finally:
                reply.close()
            self.close_connection = True

    return Handler


def serve(library: str, host: str = "127.0.0.1", port: int = 8765, open_browser: bool = True):
    app = App(library, local_only=host in ("127.0.0.1", "localhost", "::1"))
    server = ThreadingHTTPServer((host, port), make_handler(app))
    url = f"http://{'localhost' if host in ('127.0.0.1', '0.0.0.0') else host}:{port}/"
    print(f"llmfile is running at {url}")
    print(f"Your .llm files are saved in {app.library}")
    print("Press Ctrl+C to stop.")
    if open_browser:
        threading.Timer(0.5, webbrowser.open, [url]).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print()
    finally:
        server.server_close()
