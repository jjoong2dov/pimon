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


# --- Monitor.run / stop: 가짜 시계와 주입한 대기 함수로 실제 대기 없이 ---

class FakeTime:
    """clock() 은 가짜 시각, wait(t) 는 시각을 t 만큼 앞당기고 기록한다. stop_after 번째 wait 에서 멈춘다."""

    def __init__(self, m=None, stop_after=5):
        self.t = 1000.0
        self.waits = []
        self.m = m
        self.stop_after = stop_after

    def clock(self):
        return self.t

    def wait(self, timeout):
        self.waits.append(round(timeout, 6))
        self.t += timeout
        if len(self.waits) >= self.stop_after:
            self.m.stop()
        return self.m._stop.is_set()


def timed_monitor(root, interval=5, stop_after=5, tick_cost=None):
    cfg = {**DEFAULTS, "interval_s": interval}
    ft = FakeTime(stop_after=stop_after)
    m = Monitor(root, cfg, statvfs=lambda p: _Stat(), now=lambda: NOW, clock=ft.clock)
    ft.m = m
    if tick_cost is not None:                  # tick 이 걸리는 시간을 흉내
        real = m.tick
        costs = iter(tick_cost)

        def slow_tick():
            ft.t += next(costs, 0)
            return real()
        m.tick = slow_tick
    return m, ft


def test_run_ticks_every_interval_and_calls_on_status(fake_pi):
    m, ft = timed_monitor(fake_pi, interval=5, stop_after=3)
    seen = []
    m.run(on_status=lambda st: seen.append(st["overall"]), wait=ft.wait)
    assert seen == ["ok", "ok", "ok"]
    assert ft.waits == [5, 5, 5]
    assert m.status()["overall"] == "ok"


def test_run_does_not_drift_when_tick_is_slow(fake_pi):
    """tick 에 2초 걸리면 대기는 3초. 간격(5초)보다 오래 걸리면 밀린 만큼 건너뛰고 다음 칸에 맞춘다."""
    m, ft = timed_monitor(fake_pi, interval=5, stop_after=4, tick_cost=[2, 0, 7, 1])
    starts = []
    m.run(on_status=lambda st: starts.append(ft.t), wait=ft.wait)
    assert ft.waits == [3, 5, 3, 4]
    # tick 시작 칸: 1000, 1005, 1010 (7초 걸려 1015 칸은 건너뜀), 1020. 값은 tick 이 끝난 시각
    assert starts == [1002, 1005, 1017, 1021]


def test_run_keeps_going_after_tick_error(fake_pi, caplog):
    m, ft = timed_monitor(fake_pi, stop_after=3)
    real = m.tick
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("뜻밖의 오류")
        return real()
    m.tick = flaky
    seen = []
    m.run(on_status=lambda st: seen.append(st["overall"]), wait=ft.wait)
    assert len(calls) == 3
    assert seen == ["ok", "ok"]                  # 실패한 tick 은 콜백 없음
    assert "뜻밖의 오류" in caplog.text


def test_run_keeps_going_after_callback_error(fake_pi, caplog):
    m, ft = timed_monitor(fake_pi, stop_after=3)
    calls = []

    def bad(st):
        calls.append(st)
        raise ValueError("콜백 오류")
    m.run(on_status=bad, wait=ft.wait)
    assert len(calls) == 3
    assert "콜백 오류" in caplog.text


def test_run_follows_fake_pi_changes(fake_pi):
    m, ft = timed_monitor(fake_pi, stop_after=2)
    seen = []

    def on_status(st):
        seen.append(st["overall"])
        make(fake_pi, temp=82)
    m.run(on_status=on_status, wait=ft.wait)
    assert seen == ["ok", "crit"]


def test_stop_before_run_returns_without_tick(fake_pi):
    m = monitor(fake_pi)
    m.stop()
    m.run(on_status=lambda st: pytest.fail("tick 하면 안 됨"))
    assert m.status() is None


def test_stop_from_other_thread_is_immediate(fake_pi):
    """실제 Event.wait: interval 60초라도 stop() 하면 곧바로 돌아온다."""
    import threading
    import time
    m = Monitor(fake_pi, {**DEFAULTS, "interval_s": 60}, statvfs=lambda p: _Stat())
    ticked = threading.Event()
    t = threading.Thread(target=m.run, kwargs={"on_status": lambda st: ticked.set()})
    t.start()
    assert ticked.wait(2)
    start = time.monotonic()
    m.stop()
    t.join(2)
    assert not t.is_alive()
    assert time.monotonic() - start < 1


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


def test_once_does_not_write(fake_pi, tmp_path, capsys):
    before = snapshot(fake_pi)
    run(capsys, "--root", str(fake_pi), "--config", str(tmp_path / "없음.toml"), "--once")
    assert snapshot(fake_pi) == before
