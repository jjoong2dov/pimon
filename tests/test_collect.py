import os
import subprocess

import make_fake
from pimon import collect


def test_normal_pi(pi):
    cfg = {"root": str(pi), "throttle_source": "auto", "disk_path": "/", "iface": "eth0"}
    got = collect.collect(cfg)
    assert got["temp"] == {"value": 48.3, "unit": "C"}
    assert got["throttled"] == {"value": "0x0", "flags": []}
    assert got["link"] == {"value": "up", "speed": 1000}
    assert got["mem"]["value"] == round(100 * (3884 - 3200) / 3884, 1)
    assert got["load"]["value"] == 0.12
    assert 0 <= got["disk"]["value"] <= 100


def test_missing_file_is_error_not_exception(tmp_path):
    got = collect.temp(tmp_path)
    assert got["error"].startswith("FileNotFoundError")


def test_garbage_value_is_error(pi):
    (pi / "sys/class/thermal/thermal_zone0/temp").write_text("abc\n")
    assert collect.temp(pi)["error"].startswith("ValueError")


def test_throttled_bits():
    got = collect.decode_throttled(0x50005)
    assert got["value"] == "0x50005"
    assert got["flags"] == ["under_voltage", "throttled", "under_voltage_occurred", "throttled_occurred"]


def test_throttled_falls_back_to_vcgencmd(tmp_path, monkeypatch):
    def fake_run(cmd, **kw):
        assert cmd == ["vcgencmd", "get_throttled"]
        return subprocess.CompletedProcess(cmd, 0, stdout="throttled=0x20000\n")
    monkeypatch.setattr(subprocess, "run", fake_run)
    assert collect.throttled(tmp_path, "auto")["flags"] == ["freq_capped_occurred"]


def test_throttled_vcgencmd_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))
    assert "error" in collect.throttled(tmp_path, "vcgencmd")


def test_throttled_none_is_skipped(pi):
    assert collect.throttled(pi, "none") == {"skipped": True}


def test_link_down_has_no_speed(pi):
    make_fake.make(pi, link="down")
    # 실제 파이(커널 6.18)의 연결하지 않은 wlan0 에서는 down 일 때 speed 읽기가 EINVAL 이었다
    os.remove(pi / "sys/class/net/eth0/speed")
    assert collect.link(pi, "eth0") == {"value": "down"}


def test_unknown_iface(pi):
    assert "error" in collect.link(pi, "wlan9")


def test_disk_percent_like_df(tmp_path):
    class St:
        f_frsize = 4096
        f_blocks = 1000
        f_bfree = 300
        f_bavail = 250   # 예약 블록 50
    got = collect.disk(tmp_path, "/", statvfs=lambda p: St)
    # used=700, 쓸 수 있는 전체=700+250
    assert got["value"] == round(100 * 700 / 950, 1)


def test_throttled_auto_falls_back_when_sysfs_garbage(pi, monkeypatch):
    (pi / collect.THROTTLE_SYSFS).write_text("zz\n")
    monkeypatch.setattr(subprocess, "run",
                        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, stdout="throttled=0x0\n"))
    assert collect.throttled(pi, "auto")["value"] == "0x0"


def test_throttled_auto_reports_both_failures(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))
    err = collect.throttled(tmp_path, "auto")["error"]
    assert "sysfs:" in err and "vcgencmd:" in err


def test_meminfo_broken_is_error(pi):
    (pi / "proc/meminfo").write_text("MemTotal: 100 kB\n")
    assert collect.mem(pi)["error"].startswith("KeyError")
    (pi / "proc/meminfo").write_text("MemTotal: 0 kB\nMemAvailable: 0 kB\n")
    assert collect.mem(pi)["error"].startswith("ValueError")
    (pi / "proc/meminfo").write_text("MemTotal:\n")
    assert collect.mem(pi)["error"].startswith("IndexError")


def test_unknown_speed_is_left_out(pi):
    (pi / "sys/class/net/eth0/speed").write_text("-1\n")
    assert collect.link(pi, "eth0") == {"value": "up"}
