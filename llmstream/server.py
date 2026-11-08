"""Local web server: serves the UI and streams chat, inspection and download progress.

Binds to 127.0.0.1 by default. Requests must come from the page itself: the Host
header is checked (against DNS rebinding) and every POST needs an
`X-LLMStream: 1` header, which other websites can't send without a CORS
preflight that this server never approves.
"""

from __future__ import annotations

import json
import mimetypes
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Iterator
from urllib.parse import parse_qs, unquote, urlparse

from . import __version__
from .backends import BackendError, HuggingFaceBackend, LlamaCppBackend, OllamaBackend
from .backends.ollama import import_gguf_with_cli
from .config import Settings
from .downloads import Downloader, DownloadError, local_models
from .probe import Inspector, PlanStore
from .sources import SourceError
from .system import snapshot

STATIC = Path(__file__).parent / "static"
MAX_JSON = 4 * 1024 * 1024
MAX_HEADER_UPLOAD = 64 * 1024 * 1024


class App:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.ollama = OllamaBackend(settings.ollama_url)
        self.llamacpp = LlamaCppBackend(settings.llamacpp_url, settings.llama_server_bin)
        self.hf = HuggingFaceBackend(settings.hf_router_url, settings.hf_token)
        self.backends = {b.name: b for b in (self.ollama, self.llamacpp, self.hf)}
        self.plans = PlanStore()
        self.inspector = Inspector(settings, self.plans, self.ollama)
        self.downloader = Downloader(settings, self.ollama)

    def status(self) -> dict:
        results: dict = {}

        def check(name, backend):
            results[name] = backend.status()

        threads = [threading.Thread(target=check, args=item) for item in self.backends.items()]
        for t in threads:
            t.start()
        for t in threads:
            t.join(6)
        return {
            "version": __version__,
            "backends": {n: results.get(n, {"available": False, "detail": "timed out"}) for n in self.backends},
            "system": snapshot(self.settings.models_dir, self.settings.llama_server_bin),
        }

    def model_path(self, raw: str) -> Path:
        path = Path(raw).expanduser().resolve()
        root = self.settings.models_dir.resolve()
        if root not in path.parents or path.suffix.lower() != ".gguf" or not path.is_file():
            raise BackendError(f"pick a .gguf file inside {root}")
        return path


class ClientError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def make_handler(app: App, allowed_hosts: set[str]):
    class Handler(BaseHTTPRequestHandler):
        server_version = f"llmstream/{__version__}"

        def log_message(self, fmt, *args):  # quieter console
            if not str(args[0] if args else "").startswith(("GET /api/jobs", "GET /static")):
                super().log_message(fmt, *args)

        # ---------------------------------------------------------- plumbing
        def _guard(self, post: bool):
            host = (self.headers.get("Host") or "").lower()
            if host not in allowed_hosts:
                raise ClientError("unexpected Host header", HTTPStatus.FORBIDDEN)
            origin = self.headers.get("Origin")
            if origin and urlparse(origin).netloc.lower() not in allowed_hosts:
                raise ClientError("cross-origin request refused", HTTPStatus.FORBIDDEN)
            if post and self.headers.get("X-LLMStream") != "1":
                raise ClientError("missing X-LLMStream header", HTTPStatus.FORBIDDEN)

        def _json(self, obj, status=200):
            body = json.dumps(obj).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _body_json(self) -> dict:
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_JSON:
                raise ClientError("request too large", HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
            raw = self.rfile.read(length) if length else b"{}"
            try:
                data = json.loads(raw or b"{}")
            except ValueError:
                raise ClientError("invalid JSON") from None
            if not isinstance(data, dict):
                raise ClientError("expected a JSON object")
            return data

        def _stream(self, events: Iterator[dict]):
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            gen = iter(events)
            try:
                for ev in gen:
                    self.wfile.write((json.dumps(ev) + "\n").encode("utf-8"))
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass  # the person pressed Stop / closed the tab
            except (BackendError, DownloadError, OSError) as exc:
                self._try_write({"type": "error", "message": str(exc)})
            except Exception as exc:  # noqa: BLE001
                self._try_write({"type": "error", "message": f"{type(exc).__name__}: {exc}"})
            finally:
                close = getattr(gen, "close", None)
                if close:
                    close()

        def _try_write(self, ev):
            try:
                self.wfile.write((json.dumps(ev) + "\n").encode("utf-8"))
                self.wfile.flush()
            except OSError:
                pass

        def _handle(self, post: bool):
            try:
                self._guard(post)
                url = urlparse(self.path)
                (self._post if post else self._get)(url.path, parse_qs(url.query))
            except ClientError as exc:
                self._json({"error": str(exc)}, exc.status)
            except (BackendError, DownloadError, SourceError) as exc:
                self._json({"error": str(exc)}, 400)
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception as exc:  # noqa: BLE001
                self._json({"error": f"{type(exc).__name__}: {exc}"}, 500)

        def do_GET(self):  # noqa: N802
            self._handle(post=False)

        def do_POST(self):  # noqa: N802
            self._handle(post=True)

        # ---------------------------------------------------------- routes
        def _get(self, path: str, query: dict):
            if path in ("/", "/index.html"):
                return self._static("index.html")
            if path.startswith("/static/"):
                return self._static(path[len("/static/"):])
            if path == "/api/status":
                return self._json(app.status())
            if path == "/api/models":
                backend = (query.get("backend") or [""])[0]
                if backend == "local":
                    return self._json({"models": local_models(app.settings.models_dir)})
                if backend not in app.backends:
                    raise ClientError("unknown backend")
                return self._json({"models": app.backends[backend].list_models()})
            if path == "/api/jobs":
                return self._json({"jobs": app.downloader.list()})
            if path.startswith("/api/jobs/") and path.endswith("/events"):
                job = app.downloader.get(path.split("/")[3])
                if not job:
                    raise ClientError("no such job", 404)
                return self._stream(job.events())
            raise ClientError("not found", 404)

        def _post(self, path: str, query: dict):
            if path == "/api/chat":
                data = self._body_json()
                backend = app.backends.get(data.get("backend"))
                if not backend:
                    raise ClientError("unknown backend (WebLLM runs in the browser, not here)")
                model = data.get("model")
                messages = data.get("messages")
                if not model or not isinstance(messages, list):
                    raise ClientError("model and messages are required")
                return self._stream(backend.chat(model, messages, data.get("options") or {}))

            if path == "/api/inspect":
                data = self._body_json()
                return self._json(app.inspector.inspect(str(data.get("input", ""))))

            if path == "/api/inspect-file":
                length = int(self.headers.get("Content-Length") or 0)
                if length > MAX_HEADER_UPLOAD:
                    raise ClientError("send only the first part of the file", 413)
                name = unquote(self.headers.get("X-Filename") or "model.gguf")
                size = int(self.headers.get("X-File-Size") or length)
                return self._json(app.inspector.inspect_upload_header(self.rfile.read(length), name, size))

            if path == "/api/download":
                data = self._body_json()
                if data.get("confirm") is not True:
                    raise ClientError("downloads need explicit confirmation")
                plan = app.plans.get(str(data.get("plan_id", "")))
                if not plan:
                    raise ClientError("that check has expired; inspect the model again", 410)
                job = app.downloader.start(plan)
                return self._json({"job": job.snapshot()})

            if path == "/api/import":
                if (query.get("confirm") or [""])[0] != "1":
                    raise ClientError("imports need explicit confirmation")
                plan = app.plans.get((query.get("plan_id") or [""])[0])
                if not plan:
                    raise ClientError("that check has expired; drop the file again", 410)
                length = int(self.headers.get("Content-Length") or 0)
                return self._json(app.downloader.receive_upload(plan, self.rfile, length))

            if path.startswith("/api/jobs/") and path.endswith("/cancel"):
                return self._json({"cancelled": app.downloader.cancel(path.split("/")[3])})

            if path == "/api/llamacpp/load":
                data = self._body_json()
                model = app.model_path(str(data.get("path", "")))
                ctx = int(data.get("context") or 4096)
                return self._json(app.llamacpp.start(model, context=max(512, min(ctx, 262144))))

            if path == "/api/llamacpp/stop":
                app.llamacpp.stop()
                return self._json({"stopped": True})

            if path == "/api/ollama/import":
                data = self._body_json()
                model = app.model_path(str(data.get("path", "")))
                name = str(data.get("name") or model.stem.lower())
                return self._json({"ollama_model": import_gguf_with_cli(name, str(model))})

            raise ClientError("not found", 404)

        def _static(self, rel: str):
            target = (STATIC / rel).resolve()
            if STATIC.resolve() not in target.parents or not target.is_file():
                raise ClientError("not found", 404)
            body = target.read_bytes()
            ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype in ("application/javascript",):
                ctype += "; charset=utf-8"
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(body)

    return Handler


def serve(settings: Settings, host: str = "127.0.0.1", port: int = 8765, open_browser: bool = True) -> None:
    app = App(settings)
    httpd = ThreadingHTTPServer((host, port), None)  # type: ignore[arg-type]
    port = httpd.server_address[1]
    names = {"127.0.0.1", "localhost", "[::1]"}
    if host not in ("0.0.0.0", "::", "127.0.0.1", "localhost"):
        names.add(host)
    allowed = {f"{n}:{port}" for n in names}
    httpd.RequestHandlerClass = make_handler(app, allowed)
    httpd.daemon_threads = True
    url = f"http://127.0.0.1:{port}/"
    print(f"llmstream {__version__} running at {url}")
    print(f"  models folder: {settings.models_dir}")
    print("  press Ctrl+C to stop")
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")
    finally:
        app.llamacpp.stop()
        httpd.server_close()
