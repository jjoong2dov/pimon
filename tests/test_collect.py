import os
from pathlib import Path

import pytest

from fake.make_fake import make
from pimon import collect
from pimon.config import DEFAULTS

TEMP = "sys/class/thermal/thermal_zone0/temp"
THROTTLED = "sys/devices/platform/soc/soc:firmware/get_throttled"
NET = "sys/class/net/eth0"


def ok(item):
    assert item["error"] is None, item["error"]
    return item["value"]


def bad(item, *words):
    assert item["value"] is None
    assert isinstance(item["error"], str) and item["error"]
    for w in words:
        assert w in item["error"]


def snapshot(root):
    return {p: p.read_bytes() for p in Path(root).rglob("*") if p.is_file()}


# --- temp ---

def test_temp(fake_pi):
    assert ok(collect.collect_temp(fake_pi)) == {"c": 48.3}


def test_temp_changes(fake_pi):
    make(fake_pi, temp=82)
    assert ok(collect.collect_temp(fake_pi)) == {"c": 82.0}


def test_temp_root_as_str(fake_pi):
    assert ok(collect.collect_temp(str(fake_pi))) == {"c": 48.3}


def test_temp_missing(fake_pi):
    (fake_pi / TEMP).unlink()
    bad(collect.collect_temp(fake_pi), "FileNotFoundError", "thermal_zone0/temp")


def test_temp_garbage(fake_pi):
    (fake_pi / TEMP).write_text("뜨거움\n")
    bad(collect.collect_temp(fake_pi), "thermal_zone0/temp")


# --- throttle ---

def test_throttle_none(fake_pi):
    assert ok(collect.collect_throttle(fake_pi)) == {"raw": "0x0", "now": [], "past": []}


def test_throttle_past_only(fake_pi):
    make(fake_pi, throttled="0x50000")
    assert ok(collect.collect_throttle(fake_pi)) == {
        "raw": "0x50000", "now": [], "past": ["under_voltage", "throttled"]}


def test_throttle_now_and_past(fake_pi):
    make(fake_pi, throttled="0x50005")
    assert ok(collect.collect_throttle(fake_pi)) == {
        "raw": "0x50005", "now": ["under_voltage", "throttled"],
        "past": ["under_voltage", "throttled"]}


def test_throttle_all_bits(fake_pi):
    make(fake_pi, throttled="0xf000f")
    names = ["under_voltage", "freq_capped", "throttled", "soft_temp_limit"]
    assert ok(collect.collect_throttle(fake_pi)) == {"raw": "0xf000f", "now": names, "past": names}


def test_throttle_missing(fake_pi):
    (fake_pi / THROTTLED).unlink()
    bad(collect.collect_throttle(fake_pi), "get_throttled")


def test_throttle_garbage(fake_pi):
    (fake_pi / THROTTLED).write_text("zz\n")
    bad(collect.collect_throttle(fake_pi), "get_throttled")


# --- memory ---

def test_memory(fake_pi):
    assert ok(collect.collect_memory(fake_pi)) == {"total_mb": 3884, "avail_mb": 3200, "used_pct": 17.6}


def test_memory_low(fake_pi):
    make(fake_pi, mem_avail_mb=300)
    v = ok(collect.collect_memory(fake_pi))
    assert v["avail_mb"] == 300
    assert v["used_pct"] == round((3884 - 300) / 3884 * 100, 1)


def test_memory_no_available_line(fake_pi):
    (fake_pi / "proc/meminfo").write_text("MemTotal: 3977216 kB\nMemFree: 100 kB\n")
    bad(collect.collect_memory(fake_pi), "MemAvailable")


def test_memory_missing(fake_pi):
    (fake_pi / "proc/meminfo").unlink()
    bad(collect.collect_memory(fake_pi), "meminfo")


# --- disk ---

class FakeStat:
    def __init__(self, blocks, bfree, bavail, frsize=4096):
        self.f_blocks, self.f_bfree, self.f_bavail, self.f_frsize = blocks, bfree, bavail, frsize


def test_disk_joins_root_and_computes(fake_pi):
    seen = []

    def statvfs(p):
        seen.append(p)
        # 전체 10 GiB, 남음 6 GiB, 일반 사용자 가용 5.5 GiB → used 4 / (4 + 5.5)
        gib = 1024 ** 3 // 4096
        return FakeStat(10 * gib, 6 * gib, int(5.5 * gib))

    v = ok(collect.collect_disk(fake_pi, ["/"], statvfs=statvfs))
    assert [os.path.normpath(s) for s in seen] == [os.path.normpath(fake_pi)]
    assert v == [{"path": "/", "total_gb": 10.0, "used_pct": round(4 / 9.5 * 100, 1)}]


def test_disk_several_paths(fake_pi):
    (fake_pi / "boot/firmware").mkdir(parents=True)
    seen = []

    def statvfs(p):
        seen.append(os.path.normpath(p))
        return FakeStat(100, 50, 50)

    v = ok(collect.collect_disk(fake_pi, ["/", "/boot/firmware"], statvfs=statvfs))
    assert [d["path"] for d in v] == ["/", "/boot/firmware"]
    assert seen[1] == os.path.normpath(fake_pi / "boot/firmware")


def test_disk_real_statvfs(fake_pi):
    v = ok(collect.collect_disk(fake_pi, ["/"]))
    assert v[0]["path"] == "/" and 0 <= v[0]["used_pct"] <= 100


def test_disk_missing_path_is_error(fake_pi):
    bad(collect.collect_disk(fake_pi, ["/", "/없는곳"]), "없는곳")


def test_disk_statvfs_raises(fake_pi):
    def statvfs(p):
        raise PermissionError(13, "막힘", p)

    bad(collect.collect_disk(fake_pi, ["/"], statvfs=statvfs), "PermissionError")


def test_disk_empty_fs(fake_pi):
    bad(collect.collect_disk(fake_pi, ["/"], statvfs=lambda p: FakeStat(0, 0, 0)))


# --- link ---

def test_link_up(fake_pi):
    assert ok(collect.collect_link(fake_pi, "eth0")) == {
        "iface": "eth0", "up": True, "carrier": True, "speed_mbps": 1000}


def test_link_down(fake_pi):
    make(fake_pi, link="down")
    v = ok(collect.collect_link(fake_pi, "eth0"))
    assert v["up"] is False and v["carrier"] is False


def test_link_down_kernel_style(fake_pi):
    # 실제 커널은 링크가 내려가면 carrier 읽기가 실패하고 speed 는 -1 을 준다
    make(fake_pi, link="down", speed=-1)
    (fake_pi / NET / "carrier").unlink()
    assert ok(collect.collect_link(fake_pi, "eth0")) == {
        "iface": "eth0", "up": False, "carrier": None, "speed_mbps": None}


def test_link_unknown_iface(fake_pi):
    bad(collect.collect_link(fake_pi, "wlan0"), "wlan0")


# --- system ---

def test_system(fake_pi):
    assert ok(collect.collect_system(fake_pi)) == {
        "load1": 0.12, "load5": 0.12, "load15": 0.12, "uptime_s": 3600}


def test_system_missing_uptime(fake_pi):
    (fake_pi / "proc/uptime").unlink()
    bad(collect.collect_system(fake_pi), "uptime")


# 검토 3: float() 는 "nan"·"inf" 를 받아 준다. 그대로 두면 JSON 에 NaN 이 나간다.
@pytest.mark.parametrize("rel, text", [
    ("proc/loadavg", "nan 0.12 0.12 1/180 1234\n"),
    ("proc/loadavg", "0.12 inf 0.12 1/180 1234\n"),
    ("proc/uptime", "nan 100.00\n"),
    ("proc/uptime", "inf 100.00\n"),
])
def test_system_non_finite_is_error(fake_pi, rel, text):
    (fake_pi / rel).write_text(text)
    bad(collect.collect_system(fake_pi), rel)


# --- 모두 ---

def test_collect_all_keys(fake_pi):
    items = collect.collect_all(fake_pi, DEFAULTS, statvfs=lambda p: FakeStat(100, 50, 50))
    assert list(items) == ["temp", "throttle", "memory", "disk", "link", "system"]
    for item in items.values():
        assert item["error"] is None


def test_collect_all_never_raises_on_missing_root(tmp_path):
    items = collect.collect_all(tmp_path / "없음", DEFAULTS)
    assert set(items) == {"temp", "throttle", "memory", "disk", "link", "system"}
    for item in items.values():
        bad(item)


@pytest.mark.parametrize("name", ["temp", "throttle", "memory", "link", "system"])
def test_collectors_never_raise_on_empty_files(fake_pi, name):
    for p in fake_pi.rglob("*"):
        if p.is_file():
            p.write_text("")
    bad(collect.collect_all(fake_pi, DEFAULTS)[name])


def test_collect_does_not_write(fake_pi):
    before = snapshot(fake_pi)
    collect.collect_all(fake_pi, DEFAULTS)
    assert snapshot(fake_pi) == before
