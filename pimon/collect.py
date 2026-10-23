"""항목별 수집 함수. root 를 받아 그 아래에서 읽기만 한다.

모든 수집 함수는 {"value": ..., "error": None} 또는 {"value": None, "error": "..."}
를 돌려주고, 예외를 밖으로 내보내지 않는다.
"""

import functools
import os

THROTTLE_BITS = ["under_voltage", "freq_capped", "throttled", "soft_temp_limit"]
OPERSTATES = {"unknown", "notpresent", "down", "lowerlayerdown", "testing", "dormant", "up"}


def _item(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return {"value": fn(*args, **kwargs), "error": None}
        except Exception as e:
            return {"value": None, "error": f"{type(e).__name__}: {e}"}
    return wrapper


def _path(root, rel):
    return os.path.join(root, rel.lstrip("/"))


def _read(root, rel):
    with open(_path(root, rel)) as f:
        return f.read().strip()


def _parse(rel, text, conv):
    try:
        return conv(text)
    except (ValueError, IndexError):
        raise ValueError(f"{rel}: 형식 오류 {text!r}") from None


@_item
def collect_temp(root):
    rel = "sys/class/thermal/thermal_zone0/temp"
    milli = _parse(rel, _read(root, rel), int)
    return {"c": round(milli / 1000, 1)}


@_item
def collect_throttle(root):
    rel = "sys/devices/platform/soc/soc:firmware/get_throttled"
    bits = _parse(rel, _read(root, rel), lambda t: int(t, 16))
    return {
        "raw": f"0x{bits:x}",
        "now": [n for i, n in enumerate(THROTTLE_BITS) if bits >> i & 1],
        "past": [n for i, n in enumerate(THROTTLE_BITS) if bits >> (16 + i) & 1],
    }


@_item
def collect_memory(root):
    rel = "proc/meminfo"
    kb = {}
    for line in _read(root, rel).splitlines():
        key, _, rest = line.partition(":")
        if rest.split():
            kb[key.strip()] = _parse(rel, rest.split()[0], int)
    for key in ("MemTotal", "MemAvailable"):
        if key not in kb:
            raise ValueError(f"{rel}: {key} 없음")
    total, avail = kb["MemTotal"], kb["MemAvailable"]
    if total <= 0:
        raise ValueError(f"{rel}: MemTotal 이 0")
    return {
        "total_mb": total // 1024,
        "avail_mb": avail // 1024,
        "used_pct": round((total - avail) / total * 100, 1),
    }


@_item
def collect_disk(root, paths, statvfs=os.statvfs):
    out = []
    for p in paths:
        st = statvfs(_path(root, p))
        used = st.f_blocks - st.f_bfree
        usable = used + st.f_bavail      # df 와 같은 식: root 예약분은 뺀다
        if st.f_blocks <= 0 or usable <= 0:
            raise ValueError(f"{p}: 크기가 0")
        out.append({
            "path": p,
            "total_gb": round(st.f_blocks * st.f_frsize / 1024 ** 3, 1),
            "used_pct": round(used / usable * 100, 1),
        })
    return out


def _optional_int(root, rel):
    """링크가 내려가면 커널이 읽기 오류나 -1 을 주는 파일. 못 읽으면 None."""
    try:
        v = int(_read(root, rel))
    except (OSError, ValueError):
        return None
    return v if v >= 0 else None


@_item
def collect_link(root, iface):
    base = f"sys/class/net/{iface}"
    state = _read(root, f"{base}/operstate")
    if state not in OPERSTATES:
        raise ValueError(f"{base}/operstate: 형식 오류 {state!r}")
    carrier = _optional_int(root, f"{base}/carrier")
    return {
        "iface": iface,
        "up": state == "up",
        "carrier": None if carrier is None else carrier == 1,
        "speed_mbps": _optional_int(root, f"{base}/speed"),
    }


@_item
def collect_system(root):
    load = _parse("proc/loadavg", _read(root, "proc/loadavg"),
                  lambda t: [float(x) for x in t.split()[:3]])
    if len(load) != 3:
        raise ValueError(f"proc/loadavg: 형식 오류 {load!r}")
    uptime = _parse("proc/uptime", _read(root, "proc/uptime"), lambda t: float(t.split()[0]))
    return {"load1": load[0], "load5": load[1], "load15": load[2], "uptime_s": int(uptime)}


def collect_all(root, cfg, statvfs=os.statvfs):
    return {
        "temp": collect_temp(root),
        "throttle": collect_throttle(root),
        "memory": collect_memory(root),
        "disk": collect_disk(root, cfg["disk"]["paths"], statvfs=statvfs),
        "link": collect_link(root, cfg["link"]["iface"]),
        "system": collect_system(root),
    }
