"""SIGHUP 으로 설정 다시 읽기 (systemctl reload).

판정 기준값·주기·디스크 경로 등은 곧바로 반영하고, http.bind·port 와 led.enabled·state_path 는
지금 값을 유지한 채 "다시 시작해야 반영" 로그만 남긴다. 새 설정이 잘못됐으면 이전 설정을 유지한다.
"""

import signal
import threading
import time
from pathlib import Path

import pytest

from fake.make_fake import make
from pimon import __main__ as cli
from pimon import config
from pimon.config import DEFAULTS
from pimon.monitor import Monitor
from tests.test_main import (LED, finish, free_port, get_status, trigger, wait_blinking,  # noqa: F401
                             wait_for, cfg, pimon, state)
from tests.test_monitor import NOW, FakeTime, _Stat

REPO = Path(__file__).resolve().parents[1]


# --- reload_config: 무엇을 반영하고 무엇을 남기나 ---

def write(path, text):
    path.write_text(text)
    return path


def test_reload_config_applies_hot_values(tmp_path):
    cur = config.load(tmp_path / "없음.toml")
    p = write(tmp_path / "pimon.toml",
              'interval_s = 2\n[temp]\nwarn_c = 60\ncrit_c = 65\n[disk]\npaths = ["/", "/boot"]\n')
    new, pending = cli.reload_config(p, cur)
    assert pending == []
    assert new["interval_s"] == 2
    assert new["temp"] == {"warn_c": 60, "crit_c": 65}
    assert new["disk"]["paths"] == ["/", "/boot"]


def test_reload_config_keeps_restart_only_values(tmp_path):
    cur = config.load(tmp_path / "없음.toml")
    p = write(tmp_path / "pimon.toml",
              '[temp]\nwarn_c = 60\ncrit_c = 65\n[http]\nbind = "0.0.0.0"\nport = 9090\n'
              '[led]\nenabled = false\nstate_path = "/run/다른/led.json"\n')
    new, pending = cli.reload_config(p, cur)
    assert pending == ["http.bind", "http.port", "led.enabled", "led.state_path"]
    assert new["http"] == cur["http"]
    assert new["led"] == cur["led"]
    assert new["temp"]["warn_c"] == 60                 # 나머지는 반영


def test_reload_config_bad_file_raises_and_leaves_current(tmp_path):
    cur = config.load(tmp_path / "없음.toml")
    keep = {**cur}
    p = write(tmp_path / "pimon.toml", "[temp]\nwarn_c = 90\ncrit_c = 80\n")
    with pytest.raises(config.ConfigError):
        cli.reload_config(p, cur)
    assert cur == keep


# --- Monitor.reload ---

def test_reload_ticks_now_with_new_thresholds_and_interval(fake_pi):
    make(fake_pi, temp=75)
    ft = FakeTime(stop_after=4)
    m = Monitor(fake_pi, {**DEFAULTS, "interval_s": 5}, statvfs=lambda p: _Stat(),
                now=lambda: NOW, clock=ft.clock)
    ft.m = m
    seen = []

    def on_status(st):
        seen.append(st["items"]["temp"]["level"])
        if len(seen) == 1:                              # 첫 tick 뒤, 대기 전에 reload
            m.reload({**DEFAULTS, "interval_s": 2, "temp": {"warn_c": 76, "crit_c": 80}})

    m.run(on_status=on_status, wait=ft.wait)
    assert seen == ["warn", "ok", "ok", "ok"]
    # 첫 대기는 옛 주기로 잡혔지만 reload 로 깨어 곧바로 tick, 그 뒤로는 새 주기
    assert ft.waits[1:] == [2, 2, 2]


def test_reload_wakes_long_wait(fake_pi):
    """실제 Event.wait: 주기 60초 중에 reload 하면 기다리지 않고 새 설정으로 tick 한다."""
    make(fake_pi, temp=75)
    m = Monitor(fake_pi, {**DEFAULTS, "interval_s": 60}, statvfs=lambda p: _Stat())
    seen = []
    first = threading.Event()
    second = threading.Event()

    def on_status(st):
        seen.append(st["items"]["temp"]["level"])
        (second if first.is_set() else first).set()

    t = threading.Thread(target=m.run, kwargs={"on_status": on_status})
    t.start()
    try:
        assert first.wait(2)
        m.reload({**DEFAULTS, "interval_s": 60, "temp": {"warn_c": 76, "crit_c": 80}})
        assert second.wait(2)
        assert seen[:2] == ["warn", "ok"]
    finally:
        m.stop()
        t.join(2)
    assert not t.is_alive()


# --- python -m pimon 에 SIGHUP ---

def test_sighup_reloads_config(fake_pi, tmp_path, state, cfg, pimon):
    make(fake_pi, temp=82)
    port = free_port()
    c = cfg(port=port)
    proc = pimon(c)
    seen = {}

    def status():
        seen["st"] = get_status(port)
        return seen["st"]

    assert wait_for(lambda: status() is not None and seen["st"]["overall"] == "crit"), "web 이 뜨지 않음"
    wait_blinking(fake_pi, state)

    # 기준값·주기는 바꾸고, 포트와 기록 위치도 바꿔 본다 (이것들은 다시 시작해야 반영)
    other = free_port()
    c.write_text(f'interval_s = 0.1\n[http]\nbind = "127.0.0.1"\nport = {other}\n'
                 f'[led]\nstate_path = "{tmp_path / "다른.json"}"\n'
                 '[temp]\nwarn_c = 85\ncrit_c = 90\n')
    proc.send_signal(signal.SIGHUP)
    assert wait_for(lambda: (status() or {}).get("items", {}).get("temp", {}).get("level") == "ok"), \
        "새 기준값이 반영되지 않음"
    assert seen["st"]["interval_s"] == 0.1
    assert get_status(other) is None                    # 포트는 그대로
    assert proc.poll() is None

    # 잘못된 설정: 이전 설정 유지, 계속 돈다
    c.write_text("[temp]\nwarn_c = 90\ncrit_c = 80\n")
    proc.send_signal(signal.SIGHUP)
    time.sleep(0.5)
    assert proc.poll() is None
    st = get_status(port)
    assert st["items"]["temp"]["level"] == "ok" and st["interval_s"] == 0.1

    code, err = finish(proc, signal.SIGTERM)
    assert code == 0, err
    assert "다시 시작해야 반영" in err
    assert "http.port" in err and "led.state_path" in err
    assert "이전 설정 유지" in err and "temp" in err
    assert "Traceback" not in err
    assert trigger(fake_pi) == "mmc0"                   # LED 는 시작 때 기록대로 복구
    assert not state.exists()


def test_service_has_exec_reload():
    lines = [l.strip() for l in (REPO / "deploy/pimon.service").read_text().splitlines()]
    assert "ExecReload=/bin/kill -HUP $MAINPID" in lines
