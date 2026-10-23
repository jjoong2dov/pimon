"""python -m pimon 을 따로 띄워 신호·강제 종료·--restore-led 를 확인한다.

기록 파일(state_path)은 가짜 파이 폴더 밖(tmp_path/run/pimon/led.json)에 둔다.
"""

import json
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

from fake.make_fake import led_trigger_text, make
from pimon.led import parse_trigger

REPO = Path(__file__).resolve().parents[1]
LED = "sys/class/leds/ACT"
LED_FILES = {f"{LED}/trigger", f"{LED}/brightness"}


def trigger(root):
    return parse_trigger((root / LED / "trigger").read_text())


def brightness(root):
    return (root / LED / "brightness").read_text().strip()


def snapshot(root):
    return {p: p.read_bytes() for p in Path(root).rglob("*") if p.is_file()}


def changed_files(root, before):
    after = snapshot(root)
    assert set(after) == set(before)
    return {p.relative_to(root).as_posix() for p in before if before[p] != after[p]}


def wait_for(cond, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.01)
    return False


@pytest.fixture
def state(tmp_path):
    return tmp_path / "run" / "pimon" / "led.json"


@pytest.fixture
def cfg(tmp_path, state):
    def write(enabled=True, state_path=None, port=0):     # port 0: 시험이 8080 을 잡지 않게
        p = tmp_path / "pimon.toml"
        p.write_text(f'interval_s = 0.05\n[http]\nbind = "127.0.0.1"\nport = {port}\n'
                     f'[led]\nenabled = {"true" if enabled else "false"}\n'
                     f'state_path = "{state_path or state}"\n')
        return p
    return write


@pytest.fixture
def pimon(fake_pi):
    """pimon(cfg_path, *extra) -> Popen. 시험이 끝나면 남은 프로세스는 죽인다."""
    procs = []

    def start(cfg_path, *extra):
        p = subprocess.Popen([sys.executable, "-m", "pimon", "--root", str(fake_pi),
                              "--config", str(cfg_path), *extra],
                             cwd=REPO, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        procs.append(p)
        return p

    yield start
    for p in procs:
        if p.poll() is None:
            p.kill()
        p.communicate()


def run_cli(fake_pi, cfg_path, *extra):
    return subprocess.run([sys.executable, "-m", "pimon", "--root", str(fake_pi),
                           "--config", str(cfg_path), *extra],
                          cwd=REPO, capture_output=True, text=True, timeout=30)


def wait_blinking(fake_pi, state):
    seen = set()
    assert wait_for(lambda: state.exists() and trigger(fake_pi) == "none"
                    and (seen.add(brightness(fake_pi)) or seen >= {"0", "1"})), "깜빡임이 시작되지 않음"


def finish(proc, sig):
    proc.send_signal(sig)
    out, err = proc.communicate(timeout=10)
    return proc.returncode, err


@pytest.mark.parametrize("sig", [signal.SIGTERM, signal.SIGINT])
def test_signal_restores_led_and_exits_0(fake_pi, state, cfg, pimon, sig):
    make(fake_pi, temp=82)
    before = snapshot(fake_pi)
    proc = pimon(cfg())
    wait_blinking(fake_pi, state)
    assert json.loads(state.read_text())["trigger"] == "mmc0"
    code, err = finish(proc, sig)
    assert code == 0, err
    assert trigger(fake_pi) == "mmc0"
    assert not state.exists()
    assert changed_files(fake_pi, before) <= LED_FILES


def test_sigkill_then_restore_led(fake_pi, state, cfg, pimon):
    make(fake_pi, temp=82)
    before = snapshot(fake_pi)
    c = cfg()
    proc = pimon(c)
    wait_blinking(fake_pi, state)
    proc.kill()
    proc.wait(10)
    assert trigger(fake_pi) == "none"          # 프로세스 안에서는 복구할 길이 없음
    assert state.exists()

    r = run_cli(fake_pi, c, "--restore-led")
    assert r.returncode == 0, r.stderr
    assert trigger(fake_pi) == "mmc0"
    assert not state.exists()
    assert changed_files(fake_pi, before) <= LED_FILES

    r = run_cli(fake_pi, c, "--restore-led")    # 기록이 없으면 아무것도 안 하고 0 (ExecStopPost)
    assert r.returncode == 0, r.stderr
    assert trigger(fake_pi) == "mmc0"


def test_restart_after_sigkill_restores(fake_pi, state, cfg, pimon):
    make(fake_pi, temp=82)
    c = cfg()
    proc = pimon(c)
    wait_blinking(fake_pi, state)
    proc.kill()
    proc.wait(10)
    make(fake_pi, temp=48.3)                    # 다시 정상

    proc = pimon(c)
    assert wait_for(lambda: trigger(fake_pi) == "mmc0")
    assert json.loads(state.read_text())["trigger"] == "mmc0"     # none 을 원래 값으로 적지 않음
    code, err = finish(proc, signal.SIGTERM)
    assert code == 0, err
    assert trigger(fake_pi) == "mmc0"
    assert not state.exists()


def test_led_disabled_touches_nothing(fake_pi, state, cfg, pimon):
    make(fake_pi, temp=82)
    before = snapshot(fake_pi)
    proc = pimon(cfg(enabled=False))
    time.sleep(1.0)
    assert proc.poll() is None
    code, err = finish(proc, signal.SIGTERM)
    assert code == 0, err
    assert changed_files(fake_pi, before) == set()
    assert not state.exists() and not state.parent.exists()


def test_missing_led_keeps_running_without_led(fake_pi, state, cfg, pimon):
    make(fake_pi, temp=82)
    (fake_pi / LED / "trigger").unlink()
    before = snapshot(fake_pi)
    proc = pimon(cfg())
    time.sleep(1.0)
    assert proc.poll() is None                  # LED 없이도 감시는 계속
    code, err = finish(proc, signal.SIGTERM)
    assert code == 0, err
    assert "LED" in err
    assert changed_files(fake_pi, before) == set()
    assert not state.exists()


def test_unwritable_record_leaves_led_alone(fake_pi, tmp_path, cfg, pimon):
    make(fake_pi, temp=82)
    (tmp_path / "파일").write_text("디렉터리 자리에 파일\n")
    before = snapshot(fake_pi)
    proc = pimon(cfg(state_path=tmp_path / "파일" / "led.json"))
    time.sleep(1.0)
    assert proc.poll() is None
    code, err = finish(proc, signal.SIGTERM)
    assert code == 0, err
    assert "기록" in err
    assert changed_files(fake_pi, before) == set()


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def get_status(port):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/status", timeout=1) as r:
            return json.loads(r.read())
    except (OSError, ValueError):               # 아직 안 떴거나 수집 전(503)
        return None


def test_web_serves_status_while_running(fake_pi, state, cfg, pimon):
    make(fake_pi, temp=82)
    port = free_port()
    proc = pimon(cfg(port=port))
    seen = {}
    assert wait_for(lambda: seen.update(st=get_status(port)) or seen["st"] is not None), "web 이 뜨지 않음"
    assert seen["st"]["overall"] == "crit"
    assert seen["st"]["items"]["temp"]["value"]["c"] == 82
    wait_blinking(fake_pi, state)
    code, err = finish(proc, signal.SIGTERM)
    assert code == 0, err
    assert trigger(fake_pi) == "mmc0"
    assert not state.exists()
    with pytest.raises(OSError):                # 끝나면 포트도 닫힘
        socket.create_connection(("127.0.0.1", port), timeout=1).close()


def test_web_port_in_use_fails_and_restores_led(fake_pi, state, cfg, pimon):
    make(fake_pi, temp=82)
    before = snapshot(fake_pi)
    with socket.socket() as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen()
        proc = pimon(cfg(port=busy.getsockname()[1]))
        out, err = proc.communicate(timeout=10)
    assert proc.returncode == 1                 # systemd Restart=on-failure 가 다시 띄움
    assert "web" in err and "Traceback" not in err
    assert trigger(fake_pi) == "mmc0"
    assert not state.exists()
    assert changed_files(fake_pi, before) <= LED_FILES


def test_restore_led_without_record_is_noop(fake_pi, state, cfg):
    before = snapshot(fake_pi)
    r = run_cli(fake_pi, cfg(), "--restore-led")
    assert r.returncode == 0, r.stderr
    assert changed_files(fake_pi, before) == set()


def test_restore_led_with_broken_record_fails_and_writes_nothing(fake_pi, state, cfg):
    (fake_pi / LED / "trigger").write_text(led_trigger_text("none"))
    state.parent.mkdir(parents=True)
    state.write_text("{깨짐")
    before = snapshot(fake_pi)
    r = run_cli(fake_pi, cfg(), "--restore-led")
    assert r.returncode != 0
    assert "기록" in r.stderr
    assert changed_files(fake_pi, before) == set()
    assert state.exists()


@pytest.mark.parametrize("broken", ["이건 toml 아님 = = =", '[led]\nstate_path = "/다른/곳.json"\n'])
def test_sigkill_after_config_changed_restore_with_state_path(fake_pi, state, cfg, pimon, broken):
    """Codex 검토 3: 실행 중 설정이 깨지거나 state_path 가 바뀐 뒤 SIGKILL 되어도,
    --state-path 를 받은 --restore-led 는 설정을 읽지 않고 그 기록대로 되돌린다 (unit 의 ExecStopPost)."""
    make(fake_pi, temp=82)
    c = cfg()
    proc = pimon(c)
    wait_blinking(fake_pi, state)
    c.write_text(broken)
    proc.kill()
    proc.wait(10)
    assert trigger(fake_pi) == "none"

    r = run_cli(fake_pi, c, "--restore-led", "--state-path", str(state))
    assert r.returncode == 0, r.stderr
    assert trigger(fake_pi) == "mmc0"
    assert not state.exists()


def test_state_path_only_with_restore_led(fake_pi, state, cfg):
    r = run_cli(fake_pi, cfg(), "--once", "--state-path", str(state))
    assert r.returncode == 2
    assert "--state-path" in r.stderr


def test_restore_led_with_bad_config_fails(fake_pi, tmp_path):
    p = tmp_path / "pimon.toml"
    p.write_text('[led]\nstate_path = "상대경로"\n')
    r = run_cli(fake_pi, p, "--restore-led")
    assert r.returncode == 2
    assert "led.state_path" in r.stderr
