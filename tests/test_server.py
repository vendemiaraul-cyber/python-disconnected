import json
import os
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from llmstream.backends import BackendError, LlamaCppBackend, OllamaBackend
from llmstream.config import Settings
from llmstream.downloads import Downloader
from llmstream.probe import Inspector, Plan, PlanStore
from llmstream.server import App, make_handler
from tests.fakes import fake_ollama, fake_openai, file_server, make_gguf


class BackendTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ollama_srv = fake_ollama()
        cls.openai_srv = fake_openai()

    @classmethod
    def tearDownClass(cls):
        cls.ollama_srv.close()
        cls.openai_srv.close()

    def test_ollama_stream(self):
        b = OllamaBackend(self.ollama_srv.url)
        self.assertTrue(b.status()["available"])
        self.assertEqual(b.list_models()[0]["id"], "tiny:latest")
        self.assertTrue(b.installed("tiny"))
        events = list(b.chat("tiny", [{"role": "user", "content": "hi"}]))
        self.assertEqual("".join(e["text"] for e in events if e["type"] == "token"), "echo: hi")
        self.assertEqual(events[-1]["stats"]["tokens_per_second"], 3.0)

    def test_openai_stream(self):
        b = LlamaCppBackend(self.openai_srv.url)
        self.assertTrue(b.status()["available"])
        events = list(b.chat("m", [{"role": "user", "content": "hi"}]))
        self.assertEqual("".join(e["text"] for e in events if e["type"] == "token"), "Hello m")
        self.assertEqual(events[-1]["stats"]["tokens"], 3)

    def test_unreachable(self):
        b = OllamaBackend("http://127.0.0.1:9")
        self.assertFalse(b.status()["available"])
        with self.assertRaises(BackendError):
            list(b.chat("x", []))


class DownloadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["LLMSTREAM_MODELS"] = self.tmp.name
        self.settings = Settings(home=Path(self.tmp.name))
        self.payload = make_gguf({"general.architecture": "llama", "general.name": "dl"}) + os.urandom(3 * 1024 * 1024)

    def tearDown(self):
        os.environ.pop("LLMSTREAM_MODELS", None)
        self.tmp.cleanup()

    def _run(self, plan):
        d = Downloader(self.settings, OllamaBackend("http://127.0.0.1:9"))
        job = d.start(plan)
        list(job.events(heartbeat=0.2))
        return job

    def test_download_and_resume(self):
        srv = file_server(self.payload)
        try:
            dest = Path(self.tmp.name) / "x" / "m.gguf"
            dest.parent.mkdir()
            # pretend an earlier attempt stopped halfway
            (dest.parent / "m.gguf.part").write_bytes(self.payload[:1_000_000])
            plan = Plan(kind="download", label="t", size=len(self.payload),
                        files=[{"url": srv.url + "/m.gguf", "dest": str(dest), "size": len(self.payload)}], dest=str(dest))
            job = self._run(plan)
            self.assertEqual(job.state, "done", job.error)
            self.assertEqual(dest.read_bytes(), self.payload)
        finally:
            srv.close()

    def test_refuses_paths_outside_models_dir(self):
        plan = Plan(kind="download", label="t", size=10, files=[{"url": "http://127.0.0.1:9/x", "dest": "/etc/evil.gguf", "size": 10}])
        job = self._run(plan)
        self.assertEqual(job.state, "error")
        self.assertIn("outside", job.error)

    def test_url_inspect_reads_remote_header(self):
        srv = file_server(self.payload)
        try:
            plans = PlanStore()
            r = Inspector(self.settings, plans, OllamaBackend("http://127.0.0.1:9")).inspect(srv.url + "/files/dl.gguf")
            self.assertTrue(r["access"]["ok"])
            self.assertIn("architecture: llama", r["facts"])
            action = r["options"][0]["actions"][0]
            self.assertEqual(action["kind"], "download")
            self.assertIsNotNone(plans.get(action["plan_id"]))
        finally:
            srv.close()

    def test_ollama_pull(self):
        srv = fake_ollama()
        try:
            d = Downloader(self.settings, OllamaBackend(srv.url))
            job = d.start(Plan(kind="ollama-pull", label="p", size=100, ollama_model="tiny"))
            list(job.events(heartbeat=0.2))
            self.assertEqual(job.state, "done", job.error)
            self.assertEqual(job.result["ollama_model"], "tiny")
        finally:
            srv.close()


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        os.environ["LLMSTREAM_MODELS"] = cls.tmp.name
        cls.ollama_srv = fake_ollama()
        settings = Settings(home=Path(cls.tmp.name), ollama_url=cls.ollama_srv.url, llamacpp_url="http://127.0.0.1:9", hf_token=None)
        cls.app = App(settings)
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), None)
        port = cls.httpd.server_address[1]
        cls.httpd.RequestHandlerClass = make_handler(cls.app, {f"127.0.0.1:{port}", f"localhost:{port}"})
        cls.httpd.daemon_threads = True
        cls.base = f"http://127.0.0.1:{port}"
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.ollama_srv.close()
        os.environ.pop("LLMSTREAM_MODELS", None)
        cls.tmp.cleanup()

    def req(self, path, body=None, headers=None, raw=None):
        hdrs = {"X-LLMStream": "1"} if body is not None or raw is not None else {}
        hdrs.update(headers or {})
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        r = urllib.request.Request(self.base + path, data=data, headers=hdrs, method="POST" if data is not None else "GET")
        try:
            with urllib.request.urlopen(r, timeout=10) as resp:
                return resp.status, resp.read().decode()
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode()

    def test_index_and_static(self):
        status, body = self.req("/")
        self.assertEqual(status, 200)
        self.assertIn("LLM Stream", body)
        self.assertEqual(self.req("/static/app.js")[0], 200)
        self.assertEqual(self.req("/static/../server.py")[0], 404)

    def test_status_and_models(self):
        status, body = self.req("/api/status")
        data = json.loads(body)
        self.assertTrue(data["backends"]["ollama"]["available"])
        self.assertFalse(data["backends"]["llamacpp"]["available"])
        self.assertEqual(json.loads(self.req("/api/models?backend=ollama")[1])["models"][0]["id"], "tiny:latest")

    def test_chat_stream(self):
        status, body = self.req("/api/chat", {"backend": "ollama", "model": "tiny", "messages": [{"role": "user", "content": "yo"}]})
        self.assertEqual(status, 200)
        events = [json.loads(line) for line in body.splitlines()]
        self.assertEqual("".join(e.get("text", "") for e in events if e["type"] == "token"), "echo: yo")
        self.assertEqual(events[-1]["type"], "done")

    def test_chat_backend_error_is_streamed(self):
        status, body = self.req("/api/chat", {"backend": "llamacpp", "model": "x", "messages": [{"role": "user", "content": "yo"}]})
        self.assertEqual(json.loads(body.splitlines()[-1])["type"], "error")

    def test_post_needs_custom_header(self):
        r = urllib.request.Request(self.base + "/api/download", data=b"{}", method="POST", headers={"Content-Type": "application/json"})
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(r)
        self.assertEqual(ctx.exception.code, 403)

    def test_rejects_foreign_host_and_origin(self):
        self.assertEqual(self.req("/api/status", headers={"Host": "evil.example:80"})[0], 403)
        self.assertEqual(self.req("/api/inspect", {"input": "x"}, headers={"Origin": "https://evil.example"})[0], 403)

    def test_download_requires_confirmation_and_known_plan(self):
        plan = self.app.plans.add(Plan(kind="ollama-pull", label="p", size=100, ollama_model="tiny"))
        status, body = self.req("/api/download", {"plan_id": plan.id})
        self.assertEqual(status, 400)
        self.assertIn("confirmation", body)
        self.assertEqual(self.req("/api/download", {"plan_id": "made-up", "confirm": True})[0], 410)
        status, body = self.req("/api/download", {"plan_id": plan.id, "confirm": True})
        self.assertEqual(status, 200)
        job_id = json.loads(body)["job"]["job_id"]
        for _ in range(50):
            jobs = json.loads(self.req("/api/jobs")[1])["jobs"]
            if any(j["job_id"] == job_id and j["state"] == "done" for j in jobs):
                break
            time.sleep(0.1)
        else:
            self.fail("pull job didn't finish")

    def test_dropped_gguf_header_then_import(self):
        payload = make_gguf({"general.architecture": "llama", "general.name": "Dropped", "general.file_type": 7}) + b"\0" * 5000
        status, body = self.req("/api/inspect-file", raw=payload[:300], headers={"X-Filename": "my%20model.gguf", "X-File-Size": str(len(payload))})
        self.assertEqual(status, 200, body)
        report = json.loads(body)
        self.assertEqual(report["title"], "Dropped")
        self.assertIn("quantization: Q8_0", report["facts"])
        action = report["options"][0]["actions"][0]
        self.assertEqual(action["kind"], "import")
        self.assertEqual(self.req(f"/api/import?plan_id={action['plan_id']}", raw=payload)[0], 400)  # no confirm
        status, body = self.req(f"/api/import?plan_id={action['plan_id']}&confirm=1", raw=payload)
        self.assertEqual(status, 200, body)
        saved = Path(json.loads(body)["path"])
        self.assertEqual(saved.read_bytes(), payload)
        self.assertTrue(saved.name.startswith("my_model"))
        local = json.loads(self.req("/api/models?backend=local")[1])["models"]
        self.assertTrue(any(m["id"] == str(saved) for m in local))

    def test_llamacpp_load_rejects_outside_paths(self):
        status, body = self.req("/api/llamacpp/load", {"path": "/etc/passwd"})
        self.assertEqual(status, 400)


if __name__ == "__main__":
    unittest.main()
