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
    cfgfile.write_text(f'root = "{pi}"\ninterval = 1\nlisten = "127.0.0.1:18931"\n')
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
