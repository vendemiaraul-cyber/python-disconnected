"""What this machine has: memory, free disk, and which local runtimes are installed."""

from __future__ import annotations

import ctypes
import os
import platform
import shutil
import subprocess
from pathlib import Path

GB = 1024**3


def memory() -> dict:
    """Return {"total": bytes, "available": bytes}; values are 0 when unknown."""
    system = platform.system()
    try:
        if system == "Linux":
            info = {}
            with open("/proc/meminfo", encoding="ascii") as fh:
                for line in fh:
                    key, _, rest = line.partition(":")
                    info[key] = int(rest.split()[0]) * 1024
            total = info.get("MemTotal", 0)
            avail = info.get("MemAvailable", info.get("MemFree", 0))
            return {"total": total, "available": avail}
        if system == "Darwin":
            total = int(subprocess.check_output(["sysctl", "-n", "hw.memsize"], text=True).strip())
            out = subprocess.check_output(["vm_stat"], text=True)
            page = 4096
            free = 0
            for line in out.splitlines():
                if "page size of" in line:
                    page = int(line.split("page size of")[1].split()[0])
                elif line.startswith(("Pages free", "Pages inactive", "Pages speculative", "Pages purgeable")):
                    free += int(line.split(":")[1].strip().rstrip("."))
            return {"total": total, "available": free * page}
        if system == "Windows":
            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            stat = MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))  # type: ignore[attr-defined]
            return {"total": stat.ullTotalPhys, "available": stat.ullAvailPhys}
    except Exception:  # noqa: BLE001 - best effort only
        pass
    return {"total": 0, "available": 0}


def disk_free(path: Path) -> int:
    try:
        return shutil.disk_usage(path).free
    except OSError:
        return 0


def gpu_hint() -> str | None:
    """A one-line description of a GPU if an obvious one is present (no extra packages needed)."""
    if platform.system() == "Darwin" and platform.machine() == "arm64":
        return "Apple Silicon (unified memory, Metal)"
    if shutil.which("nvidia-smi"):
        try:
            out = subprocess.check_output(
                ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"],
                text=True,
                timeout=5,
            )
            first = out.strip().splitlines()[0]
            return f"NVIDIA {first}"
        except Exception:  # noqa: BLE001
            return "NVIDIA GPU"
    return None


def runtimes(llama_server_bin: str = "llama-server") -> dict:
    return {
        "ollama_cli": shutil.which("ollama"),
        "llama_server": shutil.which(llama_server_bin) or (llama_server_bin if os.path.isfile(llama_server_bin) else None),
    }


def snapshot(models_dir: Path, llama_server_bin: str = "llama-server") -> dict:
    mem = memory()
    return {
        "os": f"{platform.system()} {platform.machine()}",
        "memory_total": mem["total"],
        "memory_available": mem["available"],
        "disk_free": disk_free(models_dir),
        "models_dir": str(models_dir),
        "gpu": gpu_hint(),
        "runtimes": runtimes(llama_server_bin),
    }


def human_size(n: float | int | None) -> str:
    if n is None:
        return "unknown size"
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit in ("B", "KB") else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"
