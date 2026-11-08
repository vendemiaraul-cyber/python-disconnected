"""Run confirmed plans: resumable file downloads, Ollama pulls and local imports."""

from __future__ import annotations

import os
import secrets
import threading
import time
from pathlib import Path
from typing import BinaryIO, Iterator

from .backends.ollama import OllamaBackend
from .config import Settings
from .nethttp import HTTPError, open_url
from .probe import Plan
from .system import disk_free, human_size

CHUNK = 1024 * 1024
TERMINAL = {"done", "error", "cancelled"}


class DownloadError(Exception):
    pass


class Job:
    def __init__(self, plan: Plan):
        self.id = secrets.token_urlsafe(8)
        self.plan = plan
        self.state = "queued"
        self.status = "waiting"
        self.completed = 0
        self.total = plan.size or 0
        self.error: str | None = None
        self.result: dict = {}
        self.started = time.time()
        self.version = 0
        self.cancel_requested = threading.Event()
        self.cond = threading.Condition()

    def update(self, **kw):
        with self.cond:
            for k, v in kw.items():
                setattr(self, k, v)
            self.version += 1
            self.cond.notify_all()

    def snapshot(self) -> dict:
        elapsed = max(time.time() - self.started, 1e-6)
        return {
            "job_id": self.id,
            "kind": self.plan.kind,
            "label": self.plan.label,
            "state": self.state,
            "status": self.status,
            "completed": self.completed,
            "total": self.total,
            "percent": round(100 * self.completed / self.total, 1) if self.total else None,
            "speed": round(self.completed / elapsed) if self.state == "running" else None,
            "error": self.error,
            "result": self.result,
        }

    def events(self, heartbeat: float = 1.0) -> Iterator[dict]:
        seen = -1
        last_sent = 0.0
        while True:
            with self.cond:
                if self.version == seen:
                    self.cond.wait(heartbeat)
                version = self.version
            now = time.time()
            if version != seen and (now - last_sent >= 0.25 or self.state in TERMINAL):
                seen = version
                last_sent = now
                snap = self.snapshot()
                yield snap
                if snap["state"] in TERMINAL:
                    return
            elif version == seen:
                yield {"job_id": self.id, "state": self.state, "heartbeat": True}


class Downloader:
    def __init__(self, settings: Settings, ollama: OllamaBackend):
        self.s = settings
        self.ollama = ollama
        self.jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def start(self, plan: Plan) -> Job:
        if plan.kind not in ("download", "ollama-pull"):
            raise DownloadError(f"plan of kind {plan.kind!r} can't be started as a download")
        job = Job(plan)
        with self._lock:
            self.jobs[job.id] = job
        target = self._run_download if plan.kind == "download" else self._run_pull
        threading.Thread(target=self._wrap, args=(job, target), daemon=True, name=f"job-{job.id}").start()
        return job

    def get(self, job_id: str) -> Job | None:
        return self.jobs.get(job_id)

    def list(self) -> list[dict]:
        return [j.snapshot() for j in self.jobs.values()]

    # ------------------------------------------------------------------ runners

    def _wrap(self, job: Job, fn):
        job.update(state="running", status="starting")
        try:
            fn(job)
        except _Cancelled:
            job.update(state="cancelled", status="cancelled (partial file kept, it will resume next time)")
        except Exception as exc:  # noqa: BLE001
            job.update(state="error", status="failed", error=str(exc))

    def _check_dest(self, dest: str) -> Path:
        path = Path(dest).resolve()
        root = self.s.models_dir.resolve()
        if root not in path.parents:
            raise DownloadError(f"refusing to write outside {root}")
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def _run_download(self, job: Job):
        plan = job.plan
        paths = [self._check_dest(f["dest"]) for f in plan.files]
        have = sum(p.stat().st_size if p.exists() else (_part(p).stat().st_size if _part(p).exists() else 0) for p in paths)
        remaining = (plan.size or 0) - have
        free = disk_free(paths[0].parent)
        if plan.size and free and remaining > free:
            raise DownloadError(f"not enough disk space: {human_size(remaining)} left to download, {human_size(free)} free")

        headers = {}
        if plan.auth and self.s.hf_token:
            headers["Authorization"] = f"Bearer {self.s.hf_token}"
        done_before = 0
        for i, (f, path) in enumerate(zip(plan.files, paths), 1):
            label = f"{path.name}" + (f" (part {i}/{len(paths)})" if len(paths) > 1 else "")
            if path.exists() and (not f.get("size") or path.stat().st_size == f["size"]):
                done_before += path.stat().st_size
                job.update(completed=done_before, status=f"already have {label}")
                continue
            done_before += self._fetch(job, f["url"], path, f.get("size"), headers, done_before, label)
        job.update(state="done", status="downloaded", completed=job.total or done_before,
                   result={"path": str(paths[0]), "files": [str(p) for p in paths]})

    def _fetch(self, job: Job, url: str, path: Path, size: int | None, headers: dict, offset: int, label: str) -> int:
        part = _part(path)
        start = part.stat().st_size if part.exists() else 0
        hdrs = dict(headers)
        if start:
            hdrs["Range"] = f"bytes={start}-"
        try:
            resp = open_url(url, headers=hdrs, timeout=60)
        except HTTPError as exc:
            if exc.status == 416 and size and start >= size:
                os.replace(part, path)
                return size
            if exc.status in (401, 403):
                raise DownloadError("access denied: accept the model's license on Hugging Face and set HF_TOKEN") from None
            raise DownloadError(f"download failed: HTTP {exc.status} {exc.message}") from None
        with resp:
            if start and resp.status != 206:
                start = 0  # server ignored Range; start over
            mode = "ab" if start else "wb"
            got = start
            job.update(status=f"downloading {label}", completed=offset + got)
            with open(part, mode) as out:
                while True:
                    if job.cancel_requested.is_set():
                        raise _Cancelled
                    chunk = resp.read(CHUNK)
                    if not chunk:
                        break
                    out.write(chunk)
                    got += len(chunk)
                    job.update(completed=offset + got)
        if size and got != size:
            raise DownloadError(f"{label}: got {human_size(got)} of {human_size(size)}; try again to resume")
        os.replace(part, path)
        return got

    def _run_pull(self, job: Job):
        name = job.plan.ollama_model
        job.update(status=f"ollama pull {name}")
        layer_totals: dict[str, int] = {}
        layer_done: dict[str, int] = {}
        for ev in self.ollama.pull(name):
            if job.cancel_requested.is_set():
                raise _Cancelled
            digest = ev.get("digest")
            if digest and ev.get("total"):
                layer_totals[digest] = ev["total"]
                layer_done[digest] = ev.get("completed", 0)
            total = sum(layer_totals.values()) or job.total
            job.update(status=ev.get("status", ""), completed=sum(layer_done.values()), total=total)
            if ev.get("status") == "success":
                break
        job.update(state="done", status="pulled into Ollama", completed=job.total, result={"ollama_model": name})

    def cancel(self, job_id: str) -> bool:
        job = self.jobs.get(job_id)
        if not job or job.state in TERMINAL:
            return False
        job.cancel_requested.set()
        return True

    # ------------------------------------------------------------------ uploads

    def receive_upload(self, plan: Plan, body: BinaryIO, length: int) -> dict:
        """Copy a file the browser streams to us (a dropped local GGUF) into the models folder."""
        if plan.kind != "import":
            raise DownloadError("this plan isn't an import")
        if plan.size and length != plan.size:
            raise DownloadError("upload size doesn't match the inspected file")
        path = self._check_dest(plan.dest)
        free = disk_free(path.parent)
        if free and length > free:
            raise DownloadError(f"not enough disk space: needs {human_size(length)}, {human_size(free)} free")
        part = _part(path)
        remaining = length
        with open(part, "wb") as out:
            while remaining > 0:
                chunk = body.read(min(CHUNK, remaining))
                if not chunk:
                    break
                out.write(chunk)
                remaining -= len(chunk)
        if remaining:
            part.unlink(missing_ok=True)
            raise DownloadError("upload was interrupted")
        os.replace(part, path)
        return {"path": str(path)}


class _Cancelled(Exception):
    pass


def _part(path: Path) -> Path:
    return path.with_name(path.name + ".part")


def local_models(models_dir: Path) -> list[dict]:
    """GGUF files already in the models folder (first part only for split models)."""
    from . import gguf

    out = []
    for p in sorted(models_dir.rglob("*.gguf")):
        if "mmproj" in p.name.lower():
            continue
        if "-of-" in p.name and not p.name.split("-of-")[0].endswith("00001"):
            continue
        size = p.stat().st_size
        info = {"id": str(p), "label": p.name, "size": size, "details": str(p.parent.relative_to(models_dir))}
        try:
            summary = gguf.summarize(gguf.read_file_header(p, 4 * 1024 * 1024), size, p.name)
            bits = [summary.get("size_label"), summary.get("quantization"), summary.get("architecture")]
            info["details"] = " · ".join(b for b in bits if b) or info["details"]
        except Exception:  # noqa: BLE001
            pass
        out.append(info)
    return out
