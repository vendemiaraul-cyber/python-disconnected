"""Fake Ollama / OpenAI-compatible / file servers for tests."""

from __future__ import annotations

import json
import struct
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def make_gguf(metadata: dict, tensor_count: int = 3) -> bytes:
    """Build a minimal GGUF v3 header with the given metadata (str/int/float/bool/list[str])."""
    def s(x: str) -> bytes:
        b = x.encode()
        return struct.pack("<Q", len(b)) + b

    out = b"GGUF" + struct.pack("<I", 3) + struct.pack("<Q", tensor_count) + struct.pack("<Q", len(metadata))
    for k, v in metadata.items():
        out += s(k)
        if isinstance(v, bool):
            out += struct.pack("<I", 7) + struct.pack("<?", v)
        elif isinstance(v, int):
            out += struct.pack("<I", 4) + struct.pack("<I", v)
        elif isinstance(v, float):
            out += struct.pack("<I", 6) + struct.pack("<f", v)
        elif isinstance(v, str):
            out += struct.pack("<I", 8) + s(v)
        elif isinstance(v, list):
            out += struct.pack("<I", 9) + struct.pack("<I", 8) + struct.pack("<Q", len(v)) + b"".join(s(i) for i in v)
    return out


class _Server:
    def __init__(self, handler):
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.httpd.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class _Base(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, obj, status=200):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")


def fake_ollama():
    class H(_Base):
        def do_GET(self):
            if self.path == "/api/version":
                return self._json({"version": "0.0-test"})
            if self.path == "/api/tags":
                return self._json({"models": [{"name": "tiny:latest", "size": 1234, "details": {"parameter_size": "1B", "quantization_level": "Q4_K_M", "family": "llama"}}]})
            self._json({"error": "nope"}, 404)

        def do_POST(self):
            body = self._body()
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson")
            self.end_headers()
            if self.path == "/api/chat":
                last = body["messages"][-1]["content"]
                for word in ["echo:", " ", last]:
                    self.wfile.write((json.dumps({"message": {"role": "assistant", "content": word}, "done": False}) + "\n").encode())
                    self.wfile.flush()
                self.wfile.write((json.dumps({"done": True, "eval_count": 3, "eval_duration": 1_000_000_000}) + "\n").encode())
            elif self.path == "/api/pull":
                for ev in ({"status": "pulling manifest"},
                           {"status": "pulling abc", "digest": "sha256:abc", "total": 100, "completed": 50},
                           {"status": "pulling abc", "digest": "sha256:abc", "total": 100, "completed": 100},
                           {"status": "success"}):
                    self.wfile.write((json.dumps(ev) + "\n").encode())
    return _Server(H)


def fake_openai():
    class H(_Base):
        def do_GET(self):
            if self.path == "/health":
                return self._json({"status": "ok"})
            if self.path == "/v1/models":
                return self._json({"data": [{"id": "/models/tiny.gguf"}]})
            self._json({"error": "nope"}, 404)

        def do_POST(self):
            if self.headers.get("Authorization") == "Bearer bad":
                return self._json({"error": {"message": "bad token"}}, 401)
            body = self._body()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for piece in ["Hel", "lo ", body["model"]]:
                chunk = {"choices": [{"delta": {"content": piece}}]}
                self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
                self.wfile.flush()
            self.wfile.write(b'data: {"choices":[],"usage":{"completion_tokens":3}}\n\n')
            self.wfile.write(b"data: [DONE]\n\n")
    return _Server(H)


def file_server(payload: bytes, support_ranges: bool = True):
    class H(_Base):
        def do_HEAD(self):
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload)))
            if support_ranges:
                self.send_header("Accept-Ranges", "bytes")
            self.end_headers()

        def do_GET(self):
            rng = self.headers.get("Range")
            if rng and support_ranges:
                start, _, end = rng.split("=")[1].partition("-")
                start = int(start)
                end = int(end) if end else len(payload) - 1
                part = payload[start:end + 1]
                self.send_response(206)
                self.send_header("Content-Range", f"bytes {start}-{end}/{len(payload)}")
                self.send_header("Content-Length", str(len(part)))
                self.end_headers()
                self.wfile.write(part)
                return
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
    return _Server(H)
