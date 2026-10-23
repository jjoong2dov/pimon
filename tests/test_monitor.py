import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from fake.make_fake import make
from pimon import __main__ as cli
from pimon.config import DEFAULTS
from pimon.monitor import Monitor

KST = timezone(timedelta(hours=9))
NOW = datetime(2026, 10, 23, 10, 15, 30, tzinfo=KST)


class _Stat:
    f_blocks, f_bfree, f_bavail, f_frsize = 100, 60, 55, 4096


def monitor(root, cfg=DEFAULTS):
    return Monitor(root, cfg, statvfs=lambda p: _Stat(), now=lambda: NOW)


def snapshot(root):
    return {p: p.read_bytes() for p in Path(root).rglob("*") if p.is_file()}


# --- Monitor ---

def test_status_before_tick_is_none(fake_pi):
    assert monitor(fake_pi).status() is None


def test_tick_shape(fake_pi):
    st = monitor(fake_pi).tick()
    assert st["time"] == "2026-10-23T10:15:30+09:00"
    assert st["overall"] == "ok"
    assert list(st["items"]) == ["temp", "throttle", "memory", "disk", "link", "system"]
    assert st["items"]["temp"] == {"value": {"c": 48.3}, "error": None, "level": "ok", "reason": "48.3°C"}


def test_tick_follows_fake_pi_changes(fake_pi):
    m = monitor(fake_pi)
    assert m.tick()["overall"] == "ok"
    make(fake_pi, temp=82)
    assert m.tick()["overall"] == "crit"
    assert m.status()["items"]["temp"]["level"] == "crit"


def test_status_is_a_copy(fake_pi):
    m = monitor(fake_pi)
    m.tick()
    m.status()["items"]["temp"]["level"] = "바뀜"
    assert m.status()["items"]["temp"]["level"] == "ok"


def test_tick_on_missing_root_does_not_raise(tmp_path):
    st = monitor(tmp_path / "없음").tick()
    assert st["overall"] == "unknown"


def test_tick_does_not_write(fake_pi):
    before = snapshot(fake_pi)
    monitor(fake_pi).tick()
    assert snapshot(fake_pi) == before


def test_real_clock_has_timezone(fake_pi):
    st = Monitor(fake_pi, DEFAULTS).tick()
    assert datetime.fromisoformat(st["time"]).tzinfo is not None


# --- python -m pimon --once ---

def run(capsys, *argv):
    code = cli.main(list(argv))
    out, err = capsys.readouterr()
    return code, out, err


def test_once_prints_json(fake_pi, tmp_path, capsys):
    code, out, err = run(capsys, "--root", str(fake_pi), "--config", str(tmp_path / "없음.toml"), "--once")
    assert code == 0, err
    st = json.loads(out)
    assert st["items"]["temp"]["value"] == {"c": 48.3}
    assert st["items"]["disk"]["value"][0]["path"] == "/"     # fake_pi 폴더를 statvfs
    assert st["overall"] in ("ok", "warn", "crit")             # 디스크는 이 PC 사정에 따름


def test_once_reflects_crit(fake_pi, tmp_path, capsys):
    make(fake_pi, throttled="0x50005")
    code, out, _ = run(capsys, "--root", str(fake_pi), "--config", str(tmp_path / "없음.toml"), "--once")
    assert code == 0
    assert json.loads(out)["items"]["throttle"]["level"] == "crit"


def test_once_uses_config(fake_pi, tmp_path, capsys):
    cfg = tmp_path / "pimon.toml"
    cfg.write_text("[temp]\nwarn_c = 40\ncrit_c = 45\n")
    code, out, _ = run(capsys, "--root", str(fake_pi), "--config", str(cfg), "--once")
    assert code == 0
    assert json.loads(out)["items"]["temp"]["level"] == "crit"


def test_once_output_is_strict_json_even_with_nan_in_files(fake_pi, tmp_path, capsys):
    (fake_pi / "proc/loadavg").write_text("nan nan nan 1/180 1234\n")
    code, out, _ = run(capsys, "--root", str(fake_pi), "--config", str(tmp_path / "없음.toml"), "--once")
    assert code == 0
    st = json.loads(out, parse_constant=lambda c: pytest.fail(f"JSON 에 {c}"))
    assert st["items"]["system"]["level"] == "unknown"


def test_bad_config_exits_2_with_message(fake_pi, tmp_path, capsys):
    cfg = tmp_path / "pimon.toml"
    cfg.write_text("[temp]\nwarn_c = 90\n")
    code, out, err = run(capsys, "--root", str(fake_pi), "--config", str(cfg), "--once")
    assert code == 2
    assert out == ""
    assert "temp" in err


def test_without_once_is_not_ready_yet(fake_pi, tmp_path, capsys):
    """run 루프는 7단계. 그 전까지는 --once 없이 돌리면 아무것도 하지 않고 멈춘다."""
    code, out, err = run(capsys, "--root", str(fake_pi), "--config", str(tmp_path / "없음.toml"))
    assert code == 2
    assert "--once" in err


def test_once_does_not_write(fake_pi, tmp_path, capsys):
    before = snapshot(fake_pi)
    run(capsys, "--root", str(fake_pi), "--config", str(tmp_path / "없음.toml"), "--once")
    assert snapshot(fake_pi) == before
