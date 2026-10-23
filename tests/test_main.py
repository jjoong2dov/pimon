import json
import os
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import make_fake
from pimon import __main__ as main

LAB = Path(__file__).resolve().parent.parent


def test_once_exit_codes(pi, capsys):
    assert main.main(["once", "--root", str(pi)]) == 0
    assert json.loads(capsys.readouterr().out)["level"] == "ok"
    make_fake.make(pi, temp=75)
    assert main.main(["once", "--root", str(pi)]) == 1
    make_fake.make(pi, temp=85)
    assert main.main(["once", "--root", str(pi)]) == 2


def test_bad_config_exit_3(tmp_path, capsys):
    p = tmp_path / "x.toml"
    p.write_text("nope = 1\n")
    assert main.main(["check-config", "--config", str(p)]) == 3
    assert "모르는 설정" in capsys.readouterr().err


def wait_for(fn, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        try:
            v = fn()
            if v:
                return v
        except OSError:
            pass
        time.sleep(0.2)
    raise AssertionError("시간 초과")


def test_run_led_reload_and_stop(pi, tmp_path):
    cfgfile = tmp_path / "pimon.toml"
    cfgfile.write_text(f'root = "{pi}"\nstate_dir = "{tmp_path}/state"\ninterval = 1\nlisten = "127.0.0.1:18931"\n')
    proc = subprocess.Popen([sys.executable, "-m", "pimon", "run", "--config", str(cfgfile)],
                            cwd=LAB, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    trigger = pi / "sys/class/leds/ACT/trigger"
    try:
        status = lambda: json.load(urllib.request.urlopen("http://127.0.0.1:18931/status", timeout=2))
        assert wait_for(status)["level"] == "ok"
        make_fake.make(pi, temp=85)
        wait_for(lambda: status()["level"] == "crit")
        wait_for(lambda: trigger.read_text() == "timer\n")
        # 설정을 바꿔 85도를 정상으로 만들고 SIGHUP
        cfgfile.write_text(cfgfile.read_text() + "[limits]\ntemp_warn = 90\ntemp_crit = 95\n")
        proc.send_signal(signal.SIGHUP)
        wait_for(lambda: status()["level"] == "ok")
        wait_for(lambda: trigger.read_text() == "mmc0\n")
        make_fake.make(pi, temp=92)
        wait_for(lambda: trigger.read_text() == "timer\n")
    finally:
        proc.send_signal(signal.SIGTERM)
        out = proc.communicate(timeout=10)[0]
    assert proc.returncode == 0, out
    assert trigger.read_text() == "mmc0\n"
    assert "설정 다시 읽음" in out and "LED 원래대로 (mmc0)" in out
    assert not (tmp_path / "state/led.json").exists()


def start(pi, tmp_path, port, interval=1):
    cfgfile = tmp_path / "pimon.toml"
    cfgfile.write_text(f'root = "{pi}"\nstate_dir = "{tmp_path}/state"\ninterval = {interval}\n'
                       f'listen = "127.0.0.1:{port}"\n')
    proc = subprocess.Popen([sys.executable, "-m", "pimon", "run", "--config", str(cfgfile)],
                            cwd=LAB, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    wait_for(lambda: urllib.request.urlopen(f"http://127.0.0.1:{port}/status", timeout=2))
    return proc, cfgfile


def test_hup_then_term_still_stops(pi, tmp_path):
    proc, _ = start(pi, tmp_path, 18932, interval=30)
    proc.send_signal(signal.SIGHUP)
    proc.send_signal(signal.SIGTERM)
    try:
        out = proc.communicate(timeout=10)[0]
    except subprocess.TimeoutExpired:
        proc.kill()
        out = proc.communicate()[0]
        raise AssertionError("SIGHUP 바로 뒤 SIGTERM 에 끝나지 않음\n" + out)
    assert proc.returncode == 0, out


def test_kill_then_restart_restores_original(pi, tmp_path):
    trigger = pi / "sys/class/leds/ACT/trigger"
    make_fake.make(pi, temp=85)
    proc, cfgfile = start(pi, tmp_path, 18933)
    wait_for(lambda: trigger.read_text() == "timer\n")
    proc.kill()
    proc.wait(timeout=5)
    # 실제 커널처럼 [timer] 로 보이게 한다
    trigger.write_text("none mmc0 [timer]\n")
    make_fake.make(pi, temp=48)
    proc, _ = start(pi, tmp_path, 18933)
    proc.send_signal(signal.SIGTERM)
    out = proc.communicate(timeout=10)[0]
    assert "지난번에 남긴 기록" in out, out
    assert trigger.read_text() == "mmc0\n"


def test_restore_led_command(pi, tmp_path, capsys):
    state = tmp_path / "state"
    from pimon.led import Led
    Led(pi, state_dir=state).show("crit")
    assert main.main(["restore-led", "--root", str(pi), "--state-dir", str(state)]) == 0
    assert (pi / "sys/class/leds/ACT/trigger").read_text() == "mmc0\n"
    assert main.main(["restore-led", "--root", str(pi), "--state-dir", str(state)]) == 0
    assert "되돌릴 기록 없음" in capsys.readouterr().out


def test_health(pi, tmp_path, capsys):
    proc, cfgfile = start(pi, tmp_path, 18934)
    try:
        assert main.main(["health", "--config", str(cfgfile)]) == 0
    finally:
        proc.terminate()
        proc.communicate(timeout=10)
    assert main.main(["health", "--config", str(cfgfile)]) == 1
