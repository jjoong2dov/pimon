import copy
import os
from types import SimpleNamespace

import pytest

from fake.make_fake import make
from pimon import collect, judge
from pimon.config import DEFAULTS


def item(value):
    return {"value": value, "error": None}


ERR = {"value": None, "error": "FileNotFoundError: 없음"}


# --- 순서 ---

def test_level_order():
    assert judge.LEVELS == ["ok", "unknown", "warn", "crit"]


@pytest.mark.parametrize("levels, want", [
    (["ok"], "ok"),
    (["ok", "unknown"], "unknown"),
    (["unknown", "warn", "ok"], "warn"),
    (["warn", "crit", "unknown"], "crit"),
    ([], "unknown"),                     # 판정할 것이 없으면 모름
])
def test_worst(levels, want):
    assert judge.worst(levels) == want


# --- temp ---

@pytest.mark.parametrize("c, want", [
    (48.3, "ok"), (69.9, "ok"), (70, "warn"), (79.9, "warn"), (80, "crit"), (95.0, "crit"),
])
def test_temp_boundaries(c, want):
    level, reason = judge.judge_item("temp", item({"c": c}), DEFAULTS)
    assert level == want
    assert reason == f"{c:.1f}°C"


def test_temp_uses_config():
    cfg = copy.deepcopy(DEFAULTS)
    cfg["temp"] = {"warn_c": 50, "crit_c": 60}
    assert judge.judge_item("temp", item({"c": 55.0}), cfg)[0] == "warn"


# --- throttle ---

def thr(now=(), past=()):
    return item({"raw": "0x?", "now": list(now), "past": list(past)})


def test_throttle_none():
    assert judge.judge_item("throttle", thr(), DEFAULTS) == ("ok", "")


def test_throttle_past_only_is_warn():
    level, reason = judge.judge_item("throttle", thr(past=["under_voltage", "throttled"]), DEFAULTS)
    assert level == "warn"
    assert reason == "부팅 뒤 저전압·스로틀 있었음"


@pytest.mark.parametrize("bit", collect.THROTTLE_BITS)
def test_throttle_any_now_bit_is_crit(bit):
    level, reason = judge.judge_item("throttle", thr(now=[bit], past=[bit]), DEFAULTS)
    assert level == "crit"
    assert reason.startswith("지금 ")


def test_throttle_reason_names_all_bits():
    _, reason = judge.judge_item("throttle", thr(now=collect.THROTTLE_BITS), DEFAULTS)
    assert reason == "지금 저전압·클럭 제한·스로틀·온도 소프트 제한"


# --- memory ---

@pytest.mark.parametrize("pct, want", [
    (17.6, "ok"), (79.9, "ok"), (80.0, "warn"), (89.9, "warn"), (90.0, "crit"), (100.0, "crit"),
])
def test_memory_boundaries(pct, want):
    v = {"total_mb": 3884, "avail_mb": 1, "used_pct": pct}
    assert judge.judge_item("memory", item(v), DEFAULTS) == (want, f"{pct:.1f}%")


# --- disk ---

def disk(*pcts):
    paths = ["/", "/boot/firmware", "/data"]
    return item([{"path": p, "total_gb": 29.1, "used_pct": u} for p, u in zip(paths, pcts)])


@pytest.mark.parametrize("pct, want", [
    (41.0, "ok"), (79.9, "ok"), (80.0, "warn"), (89.9, "warn"), (90.0, "crit"),
])
def test_disk_boundaries(pct, want):
    assert judge.judge_item("disk", disk(pct), DEFAULTS) == (want, f"/ {pct:.1f}%")


def test_disk_worst_of_paths():
    level, reason = judge.judge_item("disk", disk(41.0, 85.0, 10.0), DEFAULTS)
    assert level == "warn"
    assert reason == "/ 41.0%, /boot/firmware 85.0%, /data 10.0%"


def test_disk_failed_path_does_not_hide_crit():
    """Codex 검토 6: 없는 마운트 하나 + 임계치를 넘은 마운트 하나 → crit."""
    v = item([{"path": "/", "total_gb": 29.1, "used_pct": 99.0},
              {"path": "/data", "error": "FileNotFoundError: 없음"}])
    level, reason = judge.judge_item("disk", v, DEFAULTS)
    assert level == "crit"
    assert reason == "/ 99.0%, /data 오류: FileNotFoundError: 없음"


def test_disk_failed_path_with_others_ok_is_unknown():
    v = item([{"path": "/", "total_gb": 29.1, "used_pct": 41.0},
              {"path": "/data", "error": "FileNotFoundError: 없음"}])
    assert judge.judge_item("disk", v, DEFAULTS)[0] == "unknown"


def test_disk_uses_own_thresholds_not_memory():
    cfg = copy.deepcopy(DEFAULTS)
    cfg["disk"].update(warn_used_pct=50, crit_used_pct=60)
    assert judge.judge_item("disk", disk(55.0), cfg)[0] == "warn"
    assert judge.judge_item("memory", item({"total_mb": 1, "avail_mb": 1, "used_pct": 55.0}), cfg)[0] == "ok"


# --- link ---

def test_link_up():
    v = {"iface": "eth0", "up": True, "carrier": True, "speed_mbps": 1000}
    assert judge.judge_item("link", item(v), DEFAULTS) == ("ok", "up")


@pytest.mark.parametrize("speed", [10, 100, None])
def test_link_speed_not_judged(speed):
    v = {"iface": "eth0", "up": True, "carrier": True, "speed_mbps": speed}
    assert judge.judge_item("link", item(v), DEFAULTS)[0] == "ok"


def test_judge_disk_on_fake_pi_with_missing_mount(fake_pi):
    """수집 → 판정을 이어서: 없는 마운트가 있어도 99% 마운트의 crit 가 전체 판정에 남는다."""
    full = SimpleNamespace(f_blocks=100, f_bfree=1, f_bavail=1, f_frsize=4096)
    cfg = copy.deepcopy(DEFAULTS)
    cfg["disk"]["paths"] = ["/", "/없는곳"]
    statvfs = lambda p: full if os.path.isdir(p) else os.statvfs(p)
    out = judge.judge_all(collect.collect_all(fake_pi, cfg, statvfs=statvfs), cfg)
    assert out["items"]["disk"]["level"] == "crit"
    assert out["overall"] == "crit"


def test_link_down_is_crit():
    v = {"iface": "eth0", "up": False, "carrier": None, "speed_mbps": None}
    assert judge.judge_item("link", item(v), DEFAULTS) == ("crit", "down")


# --- system ---

def test_system_always_ok():
    v = {"load1": 99.0, "load5": 99.0, "load15": 99.0, "uptime_s": 1}
    assert judge.judge_item("system", item(v), DEFAULTS) == ("ok", "")


# --- 오류 → unknown ---

@pytest.mark.parametrize("name", ["temp", "throttle", "memory", "disk", "link", "system"])
def test_error_is_unknown(name):
    assert judge.judge_item(name, ERR, DEFAULTS) == ("unknown", ERR["error"])


# --- 모두 ---

def test_judge_all_shape_and_overall():
    items = {
        "temp": item({"c": 48.3}),
        "throttle": thr(past=["under_voltage"]),
        "memory": ERR,
    }
    out = judge.judge_all(items, DEFAULTS)
    assert out["overall"] == "warn"
    assert list(out["items"]) == ["temp", "throttle", "memory"]
    assert out["items"]["temp"] == {"value": {"c": 48.3}, "error": None, "level": "ok", "reason": "48.3°C"}
    assert out["items"]["memory"]["level"] == "unknown"


def test_judge_all_does_not_modify_input():
    items = {"temp": item({"c": 48.3})}
    before = copy.deepcopy(items)
    judge.judge_all(items, DEFAULTS)
    assert items == before


def test_judge_all_on_fake_pi_normal(fake_pi):
    items = collect.collect_all(fake_pi, DEFAULTS, statvfs=lambda p: _Stat())
    out = judge.judge_all(items, DEFAULTS)
    assert out["overall"] == "ok"
    assert {n: i["level"] for n, i in out["items"].items()} == dict.fromkeys(items, "ok")


@pytest.mark.parametrize("change, name, want", [
    (dict(temp=70), "temp", "warn"),
    (dict(temp=82), "temp", "crit"),
    (dict(throttled="0x50000"), "throttle", "warn"),
    (dict(throttled="0x50005"), "throttle", "crit"),
    (dict(mem_avail_mb=300), "memory", "crit"),
    (dict(link="down"), "link", "crit"),
])
def test_judge_all_on_fake_pi_changes(fake_pi, change, name, want):
    make(fake_pi, **change)
    out = judge.judge_all(collect.collect_all(fake_pi, DEFAULTS, statvfs=lambda p: _Stat()), DEFAULTS)
    assert out["items"][name]["level"] == want
    assert out["overall"] == want


def test_judge_all_on_fake_pi_missing_file(fake_pi):
    (fake_pi / "sys/class/thermal/thermal_zone0/temp").unlink()
    out = judge.judge_all(collect.collect_all(fake_pi, DEFAULTS, statvfs=lambda p: _Stat()), DEFAULTS)
    assert out["items"]["temp"]["level"] == "unknown"
    assert out["overall"] == "unknown"


class _Stat:
    f_blocks, f_bfree, f_bavail, f_frsize = 100, 60, 55, 4096
