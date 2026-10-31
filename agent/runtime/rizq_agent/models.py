"""Free local models: what this device can run, and a one-click download through Ollama.

The owner picks a model from a short catalog. Before anything is downloaded the server
checks this device's memory and free disk. A model that would not run well is refused
with the largest one that does fit offered in its place, so a small laptop is never
filled with weights it cannot load.

Running the right model for the question is the job of the confidence gate in web.py:
the light model answers first, and when its answer cannot be verified the question goes
to the next larger installed model that this device can actually run.
"""
from __future__ import annotations

import json
import math
import os
import pathlib
import shutil
import threading
import urllib.request

OLLAMA = os.environ.get("RIZQ_OLLAMA_URL", "http://localhost:11434")

# Download sizes are what `ollama list` reports for these exact tags (31 Oct 2026).
# All are free to download and run locally.
CATALOG = (
    {"name": "qwen3.5:0.8b", "label": "خفيف جدًا — للأجهزة الضعيفة", "size_gb": 1.3, "tier": "light"},
    {"name": "qwen3.5:2b", "label": "خفيف", "size_gb": 2.7, "tier": "light"},
    {"name": "qwen3.5:4b", "label": "خفيف بجودة أعلى", "size_gb": 3.3, "tier": "light"},
    {"name": "qwen3:8b", "label": "الأساسي (موصى به)", "size_gb": 5.2, "tier": "main"},
    {"name": "qwen3.5:9b", "label": "أساسي أقوى", "size_gb": 6.6, "tier": "main"},
    {"name": "gpt-oss:20b", "label": "كبير — للمسائل الصعبة", "size_gb": 13.8, "tier": "big"},
    {"name": "qwen3.6:27b", "label": "كبير جدًا", "size_gb": 17.4, "tier": "big"},
    {"name": "qwen3:30b-a3b-instruct-2507-q4_K_M", "label": "الأكبر", "size_gb": 18.6, "tier": "big"},
)
BY_NAME = {m["name"]: m for m in CATALOG}

PULLS: dict = {}
PULLS_LOCK = threading.Lock()


def ram_needed(size_gb: float) -> float:
    """Weights plus room for the context window, the OS and the POS itself."""
    return math.ceil((size_gb * 1.15 + 2) * 10) / 10


def device() -> dict:
    ram = 0.0
    try:
        ram = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 2**30
    except (AttributeError, ValueError, OSError):
        try:                                   # Windows
            import ctypes

            class Status(ctypes.Structure):
                _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong),
                            ("total", ctypes.c_ulonglong), ("avail", ctypes.c_ulonglong),
                            ("pagefile", ctypes.c_ulonglong), ("availpage", ctypes.c_ulonglong),
                            ("virtual", ctypes.c_ulonglong), ("availvirtual", ctypes.c_ulonglong),
                            ("extended", ctypes.c_ulonglong)]
            status = Status()
            status.length = ctypes.sizeof(Status)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
            ram = status.total / 2**30
        except Exception:
            ram = 0.0
    home = pathlib.Path.home()
    disk = shutil.disk_usage(home / ".ollama" if (home / ".ollama").exists() else home).free / 2**30
    return {"ram_gb": round(ram, 1), "disk_free_gb": round(disk, 1)}


def fits(name: str, dev: dict) -> bool:
    model = BY_NAME[name]
    if dev["ram_gb"] and ram_needed(model["size_gb"]) > dev["ram_gb"]:
        return False
    return model["size_gb"] + 1 <= dev["disk_free_gb"]


def alternative(name: str, dev: dict) -> str | None:
    """The largest catalog model smaller than `name` that this device can run."""
    size = BY_NAME[name]["size_gb"]
    smaller = [m for m in CATALOG if m["size_gb"] < size and fits(m["name"], dev)]
    return max(smaller, key=lambda m: m["size_gb"])["name"] if smaller else None


def runnable(installed: list, dev: dict | None = None) -> list:
    """Installed models this device can run. Models outside the catalog are kept:
    the owner installed them on purpose and we have no size to judge them by."""
    dev = dev or device()
    return [n for n in installed if n not in BY_NAME or fits(n, dev)]


def quick(installed: list, dev: dict | None = None) -> list:
    """Installed models small enough to answer while the owner waits: at most half the
    device's memory. Larger ones load slowly and starve the POS of memory."""
    dev = dev or device()
    return [n for n in runnable(installed, dev)
            if n not in BY_NAME or not dev["ram_gb"] or BY_NAME[n]["size_gb"] <= dev["ram_gb"] / 2]


def installed(base: str = OLLAMA) -> list | None:
    """Installed model names, or None when Ollama is not running."""
    try:
        with urllib.request.urlopen(base + "/api/tags", timeout=3) as r:
            return [m["name"] for m in json.loads(r.read()).get("models", [])]
    except Exception:
        return None


def status() -> dict:
    dev = device()
    have = installed()
    with PULLS_LOCK:
        pulls = {k: dict(v) for k, v in PULLS.items()}
    rows = []
    for m in CATALOG:
        ok = fits(m["name"], dev)
        rows.append({**m, "ram_needed_gb": ram_needed(m["size_gb"]), "fits": ok,
                     "installed": bool(have and m["name"] in have),
                     "alternative": None if ok else alternative(m["name"], dev),
                     "download": pulls.get(m["name"])})
    main = [r for r in rows if r["tier"] == "main" and r["fits"]]
    light = [r for r in rows if r["fits"]]
    best = (main or light)
    return {"device": dev, "ollama": have is not None, "models": rows,
            "recommended": max(best, key=lambda r: r["size_gb"])["name"] if best else None}


def start_pull(name: str, base: str = OLLAMA, opener=urllib.request.urlopen) -> dict:
    """Start downloading `name` in the background. Refuses what this device cannot run."""
    if name not in BY_NAME:
        raise ValueError("هذا النموذج ليس في قائمة النماذج المجانية المدعومة.")
    dev = device()
    if not fits(name, dev):
        alt = alternative(name, dev)
        need = ram_needed(BY_NAME[name]["size_gb"])
        reason = (f"يحتاج تقريبًا {need} جيجا ذاكرة وجهازك فيه {dev['ram_gb']} جيجا"
                  if dev["ram_gb"] and need > dev["ram_gb"] else
                  f"يحتاج {BY_NAME[name]['size_gb'] + 1} جيجا مساحة فاضية وعندك {dev['disk_free_gb']} جيجا")
        hint = f" جرّب {alt} — يشتغل على جهازك." if alt else " ما فيه نموذج في القائمة يناسب هذا الجهاز."
        error = ValueError(f"جهازك ما يتحمل {name}: {reason}.{hint}")
        error.alternative = alt
        raise error
    if installed(base) is None:
        raise ValueError("Ollama مو شغّال. افتح تطبيق Ollama (مجاني من ollama.com) ثم أعد المحاولة.")
    with PULLS_LOCK:
        current = PULLS.get(name)
        if current and current["state"] == "downloading":
            return dict(current)
        PULLS[name] = {"state": "downloading", "percent": 0, "detail": "بدء التنزيل"}

    def run():
        try:
            request = urllib.request.Request(base + "/api/pull", method="POST",
                                             data=json.dumps({"model": name, "name": name, "stream": True}).encode(),
                                             headers={"Content-Type": "application/json"})
            with opener(request, timeout=120) as response:
                for line in response:
                    if not line.strip():
                        continue
                    event = json.loads(line)
                    if event.get("error"):
                        raise RuntimeError(str(event["error"])[:200])
                    update = {"detail": str(event.get("status", ""))[:80]}
                    if event.get("total"):
                        update["percent"] = int(100 * (event.get("completed") or 0) / event["total"])
                    if event.get("status") == "success":
                        update = {"state": "done", "percent": 100, "detail": "تم التنزيل"}
                    with PULLS_LOCK:
                        PULLS[name].update(update)
            with PULLS_LOCK:
                if PULLS[name]["state"] != "done":
                    PULLS[name].update({"state": "error", "detail": "انقطع التنزيل قبل أن يكتمل. أعد المحاولة."})
        except Exception as e:  # network, disk full, Ollama stopped
            with PULLS_LOCK:
                PULLS[name].update({"state": "error", "detail": f"فشل التنزيل: {str(e)[:160]}"})

    threading.Thread(target=run, daemon=True, name=f"pull-{name}").start()
    with PULLS_LOCK:
        return dict(PULLS[name])
