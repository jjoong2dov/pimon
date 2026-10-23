"""파이 상태 읽기. 모든 경로는 root 아래에서 찾으므로 가짜 파이 폴더로도 돈다.

수집 함수는 예외를 밖으로 내지 않는다. 읽지 못하면 {"error": 이유} 를 돌려준다.
"""

import os
import re
import subprocess
from pathlib import Path

THROTTLE_SYSFS = "sys/devices/platform/soc/soc:firmware/get_throttled"

# get_throttled 비트 (라즈베리 파이 펌웨어 문서 기준)
THROTTLE_BITS = {
    0: "under_voltage",
    1: "freq_capped",
    2: "throttled",
    3: "soft_temp_limit",
    16: "under_voltage_occurred",
    17: "freq_capped_occurred",
    18: "throttled_occurred",
    19: "soft_temp_limit_occurred",
}


def _read(root, rel):
    return (Path(root) / rel).read_text().strip()


def _guard(fn):
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except (OSError, ValueError, subprocess.SubprocessError) as e:
            return {"error": f"{type(e).__name__}: {e}"}
    wrapper.__name__ = fn.__name__
    return wrapper


@_guard
def temp(root):
    milli = int(_read(root, "sys/class/thermal/thermal_zone0/temp"))
    return {"value": round(milli / 1000, 1), "unit": "C"}


def decode_throttled(raw):
    flags = [name for bit, name in THROTTLE_BITS.items() if raw >> bit & 1]
    return {"value": f"0x{raw:x}", "flags": flags}


def _vcgencmd_throttled():
    out = subprocess.run(["vcgencmd", "get_throttled"], capture_output=True,
                         text=True, timeout=5, check=True).stdout
    m = re.search(r"throttled=(0x[0-9a-fA-F]+)", out)
    if not m:
        raise ValueError(f"vcgencmd 출력을 못 읽음: {out.strip()!r}")
    return int(m.group(1), 16)


@_guard
def throttled(root, source="auto"):
    if source == "none":
        return {"skipped": True}
    path = Path(root) / THROTTLE_SYSFS
    if source == "sysfs" or (source == "auto" and path.exists()):
        return decode_throttled(int(path.read_text().strip(), 16))
    return decode_throttled(_vcgencmd_throttled())


@_guard
def disk(root, rel="/", statvfs=os.statvfs):
    st = statvfs(Path(root) / rel.lstrip("/"))
    total = st.f_blocks * st.f_frsize
    free = st.f_bavail * st.f_frsize
    used = total - st.f_bfree * st.f_frsize
    # df 와 같은 계산: 일반 사용자가 쓸 수 있는 공간 기준
    pct = 100.0 * used / (used + free) if used + free else 0.0
    return {"value": round(pct, 1), "unit": "%", "free_mb": free // 2**20}


@_guard
def mem(root):
    info = {}
    for line in _read(root, "proc/meminfo").splitlines():
        key, _, rest = line.partition(":")
        info[key] = int(rest.split()[0])
    total, avail = info["MemTotal"], info["MemAvailable"]
    return {"value": round(100.0 * (total - avail) / total, 1), "unit": "%",
            "avail_mb": avail // 1024}


@_guard
def link(root, iface):
    base = f"sys/class/net/{iface}"
    state = _read(root, f"{base}/operstate")
    result = {"value": state}
    if state == "up":
        try:
            result["speed"] = int(_read(root, f"{base}/speed"))
        except (OSError, ValueError):
            pass
    return result


@_guard
def load(root):
    one, five, fifteen = _read(root, "proc/loadavg").split()[:3]
    uptime = float(_read(root, "proc/uptime").split()[0])
    return {"value": float(one), "load5": float(five), "load15": float(fifteen),
            "uptime_s": int(uptime)}


def collect(cfg):
    root = cfg["root"]
    return {
        "temp": temp(root),
        "throttled": throttled(root, cfg["throttle_source"]),
        "disk": disk(root, cfg["disk_path"]),
        "mem": mem(root),
        "link": link(root, cfg["iface"]),
        "load": load(root),
    }
