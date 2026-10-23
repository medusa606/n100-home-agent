"""Read-only status of the N100 host (the container shares the host kernel's /proc and /sys)."""
import glob
import json
import os
import shutil

from . import Tool, params


def _read(path: str) -> str:
    try:
        with open(path) as f:
            return f.read()
    except OSError:
        return ""


def make_tools(settings) -> list[Tool]:
    async def system_status() -> str:
        mem = {l.split(":")[0]: int(l.split()[1]) for l in _read("/proc/meminfo").splitlines() if l.split()[1:]}
        temps = [int(t) / 1000 for p in glob.glob("/sys/class/thermal/thermal_zone*/temp") if (t := _read(p).strip())]
        disk = shutil.disk_usage("/data" if os.path.exists("/data") else "/")
        uptime_s = float((_read("/proc/uptime").split() or ["0"])[0])
        return json.dumps({
            "load_avg": _read("/proc/loadavg").split()[:3],
            "mem_used_pct": round(100 * (1 - mem.get("MemAvailable", 0) / max(mem.get("MemTotal", 1), 1)), 1),
            "mem_total_gb": round(mem.get("MemTotal", 0) / 1048576, 1),
            "disk_used_pct": round(100 * disk.used / disk.total, 1),
            "disk_free_gb": round(disk.free / 1e9, 1),
            "cpu_temp_c": max(temps) if temps else None,
            "uptime_days": round(uptime_s / 86400, 1),
            "llm_model": settings.llm_model,
        })

    return [Tool("system_status", "Health of the home server: CPU load, RAM, disk, temperature, uptime.",
                 params(), system_status)]
