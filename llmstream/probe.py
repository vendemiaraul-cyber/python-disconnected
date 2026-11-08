"""Check whether a model someone dropped can be downloaded and run here.

Nothing is downloaded by this module. It returns a report with "plans"; a plan
only runs after the person confirms it (see downloads.py and the server's
/api/download endpoint).
"""

from __future__ import annotations

import json
import re
import secrets
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote

from . import gguf
from .backends.ollama import OllamaBackend
from .config import Settings
from .nethttp import HTTPError, get_json, head, open_url
from .sources import Source, parse
from .system import GB, human_size, snapshot

LEVELS = {"ok": 0, "warn": 1, "bad": 2}
_SPLIT_RE = re.compile(r"^(?P<base>.*?)-(?P<idx>\d{5})-of-(?P<total>\d{5})\.gguf$", re.IGNORECASE)
_PREFERRED_QUANTS = ["Q4_K_M", "Q4_K_S", "Q5_K_M", "IQ4_XS", "Q4_0", "Q5_K_S", "Q6_K", "Q3_K_L", "Q3_K_M", "Q8_0"]


# --------------------------------------------------------------------------- plans

@dataclass
class Plan:
    kind: str  # "download" | "ollama-pull" | "import" | "remote"
    label: str
    size: int | None = None
    files: list[dict] = field(default_factory=list)  # [{"url", "dest", "size"}]
    ollama_model: str | None = None
    remote_model: str | None = None
    dest: str | None = None  # primary file to load afterwards
    auth: bool = False  # send the HF token with requests
    id: str = field(default_factory=lambda: secrets.token_urlsafe(9))
    created: float = field(default_factory=time.time)

    def public(self) -> dict:
        return {
            "plan_id": self.id,
            "kind": self.kind,
            "label": self.label,
            "size": self.size,
            "size_text": human_size(self.size) if self.size else None,
            "dest": self.dest,
            "ollama_model": self.ollama_model,
            "remote_model": self.remote_model,
            "file_count": len(self.files),
            "needs_confirmation": self.kind in ("download", "ollama-pull", "import"),
        }


class PlanStore:
    """Plans the person may confirm. Downloads are only possible for plans made here."""

    TTL = 3600

    def __init__(self):
        self._plans: dict[str, Plan] = {}
        self._lock = threading.Lock()

    def add(self, plan: Plan) -> Plan:
        with self._lock:
            now = time.time()
            self._plans = {k: p for k, p in self._plans.items() if now - p.created < self.TTL}
            self._plans[plan.id] = plan
        return plan

    def get(self, plan_id: str) -> Plan | None:
        with self._lock:
            plan = self._plans.get(plan_id)
            if plan and time.time() - plan.created < self.TTL:
                return plan
        return None


# --------------------------------------------------------------------------- fit

def assess_fit(size: int | None, system: dict, disk_free: int | None = None, overhead: int | None = None) -> dict:
    """Will a model of `size` bytes fit on disk and in memory? Returns {"level", "messages", "memory_needed"}."""
    if not size:
        return {"level": "warn", "messages": ["Size unknown, so I can't tell whether it fits."], "memory_needed": None}
    messages: list[str] = []
    level = "ok"
    blocked = False

    def bump(new: str):
        nonlocal level
        if LEVELS[new] > LEVELS[level]:
            level = new

    disk = system.get("disk_free") if disk_free is None else disk_free
    if disk:
        if size + 256 * 1024**2 > disk:
            bump("bad")
            blocked = True
            messages.append(f"Not enough free disk: needs {human_size(size)}, {human_size(disk)} free.")
        elif size > disk * 0.8:
            bump("warn")
            messages.append(f"Uses most of your free disk ({human_size(size)} of {human_size(disk)}).")

    need = size + (overhead if overhead is not None else max(int(0.5 * GB), int(size * 0.1)))
    total, avail = system.get("memory_total") or 0, system.get("memory_available") or 0
    if total:
        if avail and need <= avail * 0.9:
            messages.append(f"Should fit in memory: ~{human_size(need)} needed, {human_size(avail)} free.")
        elif need <= total * 0.9:
            bump("warn")
            messages.append(
                f"Tight: ~{human_size(need)} needed, {human_size(avail)} free right now of {human_size(total)}. Close other apps first."
            )
        elif need <= total * 1.5:
            bump("bad")
            messages.append(
                f"Bigger than your RAM (~{human_size(need)} vs {human_size(total)}): it would swap and crawl, if it loads at all."
            )
        else:
            bump("bad")
            messages.append(f"Too big for this machine: needs ~{human_size(need)}, you have {human_size(total)}.")
    else:
        bump("warn")
        messages.append(f"Couldn't read your RAM; plan on ~{human_size(need)} of memory.")
    return {"level": level, "messages": messages, "memory_needed": need, "blocked": blocked}


def _worst(*levels: str) -> str:
    return max(levels, key=lambda lv: LEVELS[lv]) if levels else "ok"


# --------------------------------------------------------------------------- inspector

class Inspector:
    def __init__(self, settings: Settings, plans: PlanStore, ollama: OllamaBackend):
        self.s = settings
        self.plans = plans
        self.ollama = ollama

    # public -----------------------------------------------------------------

    def inspect(self, text: str) -> dict:
        src = parse(text)
        system = snapshot(self.s.models_dir, self.s.llama_server_bin)
        if src.kind == "hf":
            report = self._hf(src, system)
        elif src.kind == "ollama":
            report = self._ollama(src, system)
        elif src.kind == "url":
            report = self._url(src, system)
        else:
            report = self._local_path(src, system)
        report["source"] = {"kind": src.kind, "label": src.label(), "raw": src.raw}
        report["system"] = system
        return report

    def inspect_upload_header(self, data: bytes, filename: str, file_size: int) -> dict:
        """A GGUF file dragged from the desktop: the browser sends only the first few MB."""
        system = snapshot(self.s.models_dir, self.s.llama_server_bin)
        report = self._base_report(filename, "local")
        try:
            summary = gguf.summarize(gguf.parse_header(data), file_size, filename)
        except gguf.GGUFError as exc:
            report["access"] = {"ok": False, "status": "unreadable", "message": str(exc)}
            report["verdict"] = {"level": "bad", "headline": f"Can't use this file: {exc}"}
            report["system"] = system
            report["source"] = {"kind": "upload", "label": filename, "raw": filename}
            return report
        self._fill_local(report, summary, system, file_size)
        dest = self.s.models_dir / "imported" / _safe_name(filename)
        if summary.get("split_count") and summary["split_count"] > 1:
            report["notes"].append("This is one part of a split model. Drop the first part (-00001-of-…) and keep the parts together.")
        plan = self.plans.add(Plan(
            kind="import",
            label=f"Copy into {dest.parent}",
            size=file_size,
            dest=str(dest),
            files=[{"url": None, "dest": str(dest), "size": file_size}],
        ))
        fit = assess_fit(file_size, system, overhead=self._overhead(summary, file_size))
        report["options"] = [{
            "title": summary.get("quantization") or "GGUF",
            "size": file_size,
            "size_text": human_size(file_size),
            "fit": fit,
            "recommended": True,
            "actions": [plan.public()],
        }]
        report["verdict"] = {"level": fit["level"], "headline": self._headline(fit["level"], filename, local=True)}
        report["access"] = {"ok": True, "status": "local", "message": "It's already on your computer."}
        report["system"] = system
        report["source"] = {"kind": "upload", "label": filename, "raw": filename}
        return report

    # helpers ----------------------------------------------------------------

    @staticmethod
    def _base_report(title: str, kind: str) -> dict:
        return {
            "title": title,
            "facts": [],
            "access": {"ok": True, "status": "unknown", "message": ""},
            "options": [],
            "notes": [],
            "suggestions": [],
            "links": [],
            "verdict": {"level": "ok", "headline": ""},
            "webllm_model_id": None,
            "kind": kind,
        }

    @staticmethod
    def _headline(level: str, name: str, local: bool = False) -> str:
        if local:
            return {
                "ok": f"{name} should run on this machine.",
                "warn": f"{name} will be tight on this machine.",
                "bad": f"{name} probably won't run well on this machine.",
            }[level]
        return {
            "ok": f"{name} can be downloaded and should run on this machine.",
            "warn": f"{name} can be downloaded, but it'll be tight on this machine.",
            "bad": f"{name} probably won't run well on this machine.",
        }[level]

    @staticmethod
    def _overhead(summary: dict, size: int) -> int:
        kv = gguf.kv_cache_bytes(summary, 4096)
        return max(int(0.3 * GB), kv + int(0.3 * GB)) if kv else max(int(0.5 * GB), int(size * 0.1))

    def _hf_headers(self) -> dict:
        return {"Authorization": f"Bearer {self.s.hf_token}"} if self.s.hf_token else {}

    def _ollama_up(self) -> bool:
        return bool(self.ollama.status().get("available"))

    # Hugging Face -------------------------------------------------------------

    def _hf(self, src: Source, system: dict) -> dict:
        repo = src.repo
        hub = self.s.hf_hub_url
        report = self._base_report(repo, "hf")
        page = f"{hub}/{repo}"
        report["links"].append({"label": "Model page", "url": page})
        rev_part = "" if src.revision == "main" else f"/revision/{quote(src.revision, safe='')}"
        try:
            info = get_json(f"{hub}/api/models/{repo}{rev_part}?blobs=true", headers=self._hf_headers())
        except HTTPError as exc:
            if exc.status in (401, 404):
                msg = "Not found on Hugging Face." + ("" if self.s.hf_token else " If it's private, set HF_TOKEN.")
                report["access"] = {"ok": False, "status": "not-found", "message": msg}
            else:
                report["access"] = {"ok": False, "status": "error", "message": f"Hugging Face said: {exc.message}"}
            report["verdict"] = {"level": "bad", "headline": report["access"]["message"]}
            return report
        except OSError as exc:
            report["access"] = {"ok": False, "status": "unreachable", "message": f"Couldn't reach Hugging Face: {exc}"}
            report["verdict"] = {"level": "bad", "headline": "You seem to be offline (couldn't reach huggingface.co)."}
            return report

        files = [f for f in info.get("siblings", []) if f.get("rfilename")]
        gated = info.get("gated") or False
        gg = info.get("gguf") or {}
        st = info.get("safetensors") or {}
        params = gg.get("total") or st.get("total")
        card = info.get("cardData") or {}
        if params:
            report["facts"].append(f"{_param_text(params)} parameters")
        if gg.get("architecture"):
            report["facts"].append(f"architecture: {gg['architecture']}")
        if gg.get("context_length"):
            report["facts"].append(f"context: {gg['context_length']:,} tokens")
        if card.get("license"):
            report["facts"].append(f"license: {card['license']}")
        if info.get("downloads") is not None:
            report["facts"].append(f"{info['downloads']:,} downloads last month")
        if gated:
            report["facts"].append("gated: you must accept the license on the model page")
        if info.get("disabled"):
            report["access"] = {"ok": False, "status": "disabled", "message": "This repository is disabled."}
            report["verdict"] = {"level": "bad", "headline": "This repository is disabled on Hugging Face."}
            return report

        if repo.lower().startswith("mlc-ai/") or repo.endswith("-MLC"):
            report["webllm_model_id"] = repo.split("/", 1)[1]
            report["notes"].append("This is an MLC build for WebLLM. If it's in the WebLLM list it runs right in your browser.")

        groups = _gguf_groups(files)
        mmproj = [g for g in groups if "mmproj" in g["name"].lower()]
        groups = [g for g in groups if "mmproj" not in g["name"].lower()]
        if src.filename and src.filename.lower().endswith(".gguf"):
            wanted = _group_key(src.filename)
            groups = [g for g in groups if g["key"] == wanted] or groups
        if src.quant:
            q = [g for g in groups if (g["quant"] or "").upper() == src.quant]
            if q:
                groups = q
            else:
                report["notes"].append(f"No {src.quant} file here; showing every GGUF instead.")
        if mmproj:
            report["notes"].append("Includes a vision projector (mmproj) file; text chat works without it.")

        # Can we actually download? Probe one real file.
        probe_file = (groups[0]["files"][0]["rfilename"] if groups else
                      next((f["rfilename"] for f in files if f["rfilename"] in ("config.json", "README.md")), None))
        access = self._hf_access(repo, src.revision, probe_file, gated, info.get("private"))
        report["access"] = access

        if groups:
            self._hf_gguf_options(report, repo, src.revision, groups, system, access)
        else:
            self._hf_no_gguf(report, repo, files, params, system)

        levels = [o["fit"]["level"] for o in report["options"] if o.get("fit")]
        best = min(levels, key=lambda lv: LEVELS[lv]) if levels else ("ok" if report["options"] else "bad")
        if not access["ok"]:
            report["verdict"] = {"level": "bad", "headline": access["message"]}
        elif groups:
            report["verdict"] = {"level": best, "headline": self._headline(best, repo)}
        return report

    def _hf_access(self, repo: str, revision: str, filename: str | None, gated, private) -> dict:
        page = f"{self.s.hf_hub_url}/{repo}"
        if not filename:
            return {"ok": True, "status": "open", "message": "Public repository."}
        url = f"{self.s.hf_hub_url}/{repo}/resolve/{quote(revision, safe='')}/{quote(filename)}"
        status, _ = head(url, headers=self._hf_headers())
        if status in (200, 302, 304):
            if gated:
                return {"ok": True, "status": "gated-granted", "message": "Gated, and your token has been granted access."}
            return {"ok": True, "status": "open", "message": "Download is accessible."}
        if status in (401, 403):
            if gated:
                need = "" if self.s.hf_token else " Then set HF_TOKEN so I can download with your account."
                return {
                    "ok": False,
                    "status": "gated",
                    "message": f"Gated model: accept the license at {page} first.{need}",
                    "link": page,
                }
            if private:
                return {"ok": False, "status": "private", "message": "Private repository: set HF_TOKEN for an account that can see it."}
            return {"ok": False, "status": "denied", "message": f"Hugging Face refused the download (HTTP {status})."}
        if status == 0:
            return {"ok": False, "status": "unreachable", "message": "Couldn't reach Hugging Face to check the download."}
        return {"ok": False, "status": "error", "message": f"Download check failed (HTTP {status})."}

    def _hf_gguf_options(self, report: dict, repo: str, revision: str, groups: list[dict], system: dict, access: dict):
        ollama_up = self._ollama_up()
        owner, name = repo.split("/", 1)
        dest_dir = self.s.models_dir / f"{owner}__{name}"
        fits = {}
        for g in groups:
            fits[g["key"]] = assess_fit(g["size"], system)
        rec = _recommend(groups, fits)
        for g in sorted(groups, key=lambda x: x["size"] or 0):
            files = [{
                "url": f"{self.s.hf_hub_url}/{repo}/resolve/{quote(revision, safe='')}/{quote(f['rfilename'])}",
                "dest": str(dest_dir / Path(f["rfilename"]).name),
                "size": f.get("size"),
            } for f in g["files"]]
            actions = []
            if access["ok"]:
                plan = self.plans.add(Plan(
                    kind="download",
                    label="Download for llama.cpp",
                    size=g["size"],
                    files=files,
                    dest=files[0]["dest"],
                    auth=True,
                ))
                actions.append(plan.public())
                if g["quant"] and len(g["files"]) == 1 and ollama_up and "/" not in g["files"][0]["rfilename"]:
                    tag = f"hf.co/{repo}:{g['quant']}"
                    p2 = self.plans.add(Plan(kind="ollama-pull", label="Pull into Ollama", size=g["size"], ollama_model=tag))
                    actions.append(p2.public())
            report["options"].append({
                "title": g["quant"] or g["name"],
                "subtitle": g["name"] + (f" ({len(g['files'])} parts)" if len(g["files"]) > 1 else ""),
                "size": g["size"],
                "size_text": human_size(g["size"]),
                "fit": fits[g["key"]],
                "recommended": g["key"] == rec,
                "actions": actions,
            })
        if not ollama_up:
            report["notes"].append("Start Ollama to also get a one-click \"Pull into Ollama\" option.")

    def _hf_no_gguf(self, report: dict, repo: str, files: list[dict], params: int | None, system: dict):
        weights = [f for f in files if f["rfilename"].endswith((".safetensors", ".bin", ".pth", ".pt")) and f.get("size")]
        total = sum(f["size"] for f in weights if f["rfilename"].endswith(".safetensors")) or sum(f["size"] for f in weights)
        if total:
            report["facts"].append(f"full weights: {human_size(total)}")
        report["notes"].append(
            "No GGUF files in this repo, so llama.cpp and Ollama can't run it directly. "
            "Use a GGUF conversion (below) to run it locally, or chat with it through Hugging Face Inference."
        )
        if params:
            q4 = int(params * 0.6)
            fit = assess_fit(q4, system)
            report["notes"].append(f"A 4-bit GGUF of this model would be about {human_size(q4)}: {fit['messages'][-1]}")
            report["verdict"] = {
                "level": fit["level"],
                "headline": f"{repo} has no GGUF here. A 4-bit version would be ~{human_size(q4)}"
                            + (" and should fit." if fit["level"] == "ok" else " — see below."),
            }
        else:
            report["verdict"] = {"level": "warn", "headline": f"{repo} has no GGUF files to run locally."}

        # Quantized versions somebody else published.
        try:
            found = get_json(
                f"{self.s.hf_hub_url}/api/models?filter=base_model:quantized:{repo}&filter=gguf&sort=downloads&limit=5",
                headers=self._hf_headers(),
                timeout=15,
            )
            for m in found:
                report["suggestions"].append({"label": m["id"], "input": m["id"], "detail": f"{m.get('downloads', 0):,} downloads"})
        except Exception:  # noqa: BLE001
            pass

        # Hosted inference for the big ones.
        try:
            info = get_json(f"{self.s.hf_hub_url}/api/models/{repo}?expand[]=inferenceProviderMapping",
                            headers=self._hf_headers(), timeout=15)
            mapping = info.get("inferenceProviderMapping") or {}
            if isinstance(mapping, list):
                mapping = {m.get("provider"): m for m in mapping}
            live = [p for p, v in mapping.items() if (v or {}).get("status") == "live"
                    and (v or {}).get("task") in ("conversational", "text-generation", None)]
            if live:
                plan = self.plans.add(Plan(kind="remote", label="Chat via Hugging Face Inference", remote_model=repo))
                report["options"].append({
                    "title": "Hosted",
                    "subtitle": "runs on: " + ", ".join(sorted(live)[:6]),
                    "size": None,
                    "size_text": "no download",
                    "fit": {"level": "ok", "messages": ["Runs remotely; needs an HF token."], "memory_needed": None},
                    "recommended": report["verdict"]["level"] != "ok",
                    "actions": [plan.public()],
                })
        except Exception:  # noqa: BLE001
            pass

    # Ollama ---------------------------------------------------------------------

    def _ollama(self, src: Source, system: dict) -> dict:
        name, tag = src.model, src.tag
        ns, _, short = name.rpartition("/")
        ns = ns or "library"
        report = self._base_report(f"{name}:{tag}", "ollama")
        page = f"https://ollama.com/{'library/' + short if ns == 'library' else name}"
        report["links"].append({"label": "Model page", "url": page})
        reg = self.s.ollama_registry_url
        try:
            manifest = get_json(
                f"{reg}/v2/{ns}/{short}/manifests/{quote(tag)}",
                headers={"Accept": "application/vnd.docker.distribution.manifest.v2+json"},
            )
        except HTTPError as exc:
            msg = f"{name}:{tag} isn't in the Ollama library." if exc.status in (401, 404) else f"Ollama registry said: {exc.message}"
            report["access"] = {"ok": False, "status": "not-found", "message": msg}
            report["verdict"] = {"level": "bad", "headline": msg}
            return report
        except OSError as exc:
            report["access"] = {"ok": False, "status": "unreachable", "message": f"Couldn't reach the Ollama registry: {exc}"}
            report["verdict"] = {"level": "bad", "headline": "You seem to be offline (couldn't reach registry.ollama.ai)."}
            return report

        layers = manifest.get("layers", [])
        total = sum(layer.get("size", 0) for layer in layers)
        model_layer = next((layer for layer in layers if layer.get("mediaType", "").endswith(".model")), None)
        report["access"] = {"ok": True, "status": "open", "message": "Available from the Ollama library."}
        try:
            cfg_digest = manifest["config"]["digest"]
            with open_url(f"{reg}/v2/{ns}/{short}/blobs/{cfg_digest}", timeout=15) as resp:
                cfg = json.loads(resp.read().decode("utf-8"))
            if cfg.get("model_family"):
                report["facts"].append(f"family: {cfg['model_family']}")
            if cfg.get("model_type"):
                report["facts"].append(f"{cfg['model_type']} parameters")
            if cfg.get("file_type"):
                report["facts"].append(f"quantization: {cfg['file_type']}")
        except Exception:  # noqa: BLE001
            pass
        report["facts"].append(f"download: {human_size(total)}")

        fit = assess_fit(total, system)
        up = self._ollama_up()
        installed = up and self.ollama.installed(f"{name}:{tag}")
        actions = []
        if installed:
            report["notes"].append("Already installed in Ollama — pick it in the Ollama tab.")
        if up:
            plan = self.plans.add(Plan(kind="ollama-pull", label="Pull into Ollama", size=total, ollama_model=f"{name}:{tag}"))
            actions.append(plan.public())
        else:
            report["notes"].append("Ollama isn't running. Start it (`ollama serve`) to pull this model, or grab the GGUF for llama.cpp.")
        if model_layer:
            dest = self.s.models_dir / "ollama" / f"{short}-{_safe_name(tag)}.gguf"
            plan = self.plans.add(Plan(
                kind="download",
                label="Download GGUF for llama.cpp",
                size=model_layer["size"],
                files=[{"url": f"{reg}/v2/{ns}/{short}/blobs/{model_layer['digest']}", "dest": str(dest), "size": model_layer["size"]}],
                dest=str(dest),
            ))
            actions.append(plan.public())
        report["options"].append({
            "title": tag,
            "subtitle": f"{name}:{tag}",
            "size": total,
            "size_text": human_size(total),
            "fit": fit,
            "recommended": True,
            "actions": actions,
        })
        report["verdict"] = {"level": fit["level"], "headline": self._headline(fit["level"], f"{name}:{tag}")}
        return report

    # Direct URL -----------------------------------------------------------------

    def _url(self, src: Source, system: dict) -> dict:
        url = src.url
        report = self._base_report(src.filename or url, "url")
        report["links"].append({"label": "Link", "url": url})
        status, hdrs = head(url)
        if status not in (200, 206):
            msg = "Couldn't reach that link." if status == 0 else f"That link isn't downloadable (HTTP {status})."
            report["access"] = {"ok": False, "status": "denied", "message": msg}
            report["verdict"] = {"level": "bad", "headline": msg}
            return report
        size = int(hdrs["content-length"]) if hdrs.get("content-length", "").isdigit() else None
        report["access"] = {"ok": True, "status": "open", "message": "Download is accessible."
                            + (" Resumable." if hdrs.get("accept-ranges") == "bytes" else "")}
        summary = None
        if (src.filename or "").lower().endswith(".gguf") and hdrs.get("accept-ranges") == "bytes":
            try:
                with open_url(url, headers={"Range": "bytes=0-8388607"}, timeout=30) as resp:
                    summary = gguf.summarize(gguf.parse_header(resp.read()), size, src.filename)
                self._summary_facts(report, summary)
            except Exception:  # noqa: BLE001
                summary = None
        if size:
            report["facts"].append(f"download: {human_size(size)}")
        if not (src.filename or "").lower().endswith(".gguf"):
            report["notes"].append("This doesn't look like a GGUF model file; llama.cpp may not be able to load it.")
        fit = assess_fit(size, system, overhead=self._overhead(summary, size) if summary and size else None)
        dest = self.s.models_dir / "downloads" / _safe_name(src.filename or "model.gguf")
        plan = self.plans.add(Plan(kind="download", label="Download", size=size, files=[{"url": url, "dest": str(dest), "size": size}], dest=str(dest)))
        report["options"].append({
            "title": (summary or {}).get("quantization") or "File",
            "subtitle": src.filename,
            "size": size,
            "size_text": human_size(size),
            "fit": fit,
            "recommended": True,
            "actions": [plan.public()],
        })
        report["verdict"] = {"level": fit["level"], "headline": self._headline(fit["level"], src.filename or "This file")}
        return report

    # Local file (CLI) -----------------------------------------------------------

    def _local_path(self, src: Source, system: dict) -> dict:
        path = Path(src.path)
        report = self._base_report(path.name, "local")
        if not path.is_file():
            report["access"] = {"ok": False, "status": "missing", "message": f"No such file: {path}"}
            report["verdict"] = {"level": "bad", "headline": f"No such file: {path}"}
            return report
        size = path.stat().st_size
        try:
            summary = gguf.summarize(gguf.read_file_header(path), size, path.name)
        except gguf.GGUFError as exc:
            report["access"] = {"ok": False, "status": "unreadable", "message": str(exc)}
            report["verdict"] = {"level": "bad", "headline": str(exc)}
            return report
        self._fill_local(report, summary, system, size)
        fit = assess_fit(size, system, disk_free=10**18, overhead=self._overhead(summary, size))
        report["options"].append({
            "title": summary.get("quantization") or "GGUF",
            "subtitle": str(path),
            "size": size,
            "size_text": human_size(size),
            "fit": fit,
            "recommended": True,
            "actions": [],
            "local_path": str(path.resolve()),
        })
        report["access"] = {"ok": True, "status": "local", "message": "It's already on your computer."}
        report["verdict"] = {"level": fit["level"], "headline": self._headline(fit["level"], path.name, local=True)}
        return report

    def _fill_local(self, report: dict, summary: dict, system: dict, size: int):
        report["title"] = summary.get("name") or report["title"]
        self._summary_facts(report, summary)
        report["facts"].append(f"file: {human_size(size)}")
        if summary.get("header_truncated"):
            report["notes"].append("Only part of the header was read; some details may be missing.")
        report["gguf"] = summary

    @staticmethod
    def _summary_facts(report: dict, summary: dict):
        if summary.get("size_label") or summary.get("parameters"):
            report["facts"].append(f"{summary.get('size_label') or _param_text(summary['parameters'])} parameters")
        if summary.get("architecture"):
            report["facts"].append(f"architecture: {summary['architecture']}")
        if summary.get("quantization"):
            report["facts"].append(f"quantization: {summary['quantization']}")
        if summary.get("context_length"):
            report["facts"].append(f"context: {summary['context_length']:,} tokens")
        if summary.get("has_chat_template") is False:
            report["facts"].append("no chat template (may be a base model)")


# --------------------------------------------------------------------------- utils

def _param_text(n: int) -> str:
    if n >= 1e9:
        return f"{n / 1e9:.1f}B".replace(".0B", "B")
    if n >= 1e6:
        return f"{n / 1e6:.0f}M"
    return f"{n:,}"


def _safe_name(name: str) -> str:
    name = Path(name.replace("\\", "/")).name
    name = re.sub(r"[^\w.\-]+", "_", name).strip("._") or "model"
    return name[:200]


def _group_key(rfilename: str) -> str:
    m = _SPLIT_RE.match(rfilename)
    return m.group("base") if m else rfilename


def _gguf_groups(files: list[dict]) -> list[dict]:
    """Group GGUF files, joining split models (name-00001-of-00003.gguf) into one entry."""
    groups: dict[str, dict] = {}
    for f in files:
        rf = f["rfilename"]
        if not rf.lower().endswith(".gguf"):
            continue
        key = _group_key(rf)
        g = groups.setdefault(key, {"key": key, "name": Path(key).name, "files": [], "size": 0, "quant": None})
        g["files"].append(f)
        g["size"] += f.get("size") or (f.get("lfs") or {}).get("size") or 0
    for g in groups.values():
        g["files"].sort(key=lambda f: f["rfilename"])
        # The quant often lives in a folder name for split models (Q4_K_M/xxx-00001-of-00002.gguf).
        g["quant"] = gguf.quant_from_name(g["key"]) or gguf.quant_from_name(Path(g["key"]).parent.name or "")
        g["size"] = g["size"] or None
    return list(groups.values())


def _recommend(groups: list[dict], fits: dict) -> str | None:
    """Prefer a well-known balanced quant that fits; otherwise the biggest that fits; otherwise the smallest."""
    ok = [g for g in groups if fits[g["key"]]["level"] == "ok"]
    for q in _PREFERRED_QUANTS:
        for g in ok:
            if (g["quant"] or "").upper() == q:
                return g["key"]
    if ok:
        return max(ok, key=lambda g: g["size"] or 0)["key"]
    warn = [g for g in groups if fits[g["key"]]["level"] == "warn"]
    pool = warn or groups
    return min(pool, key=lambda g: g["size"] or 0)["key"] if pool else None


__all__ = ["Inspector", "Plan", "PlanStore", "assess_fit"]
