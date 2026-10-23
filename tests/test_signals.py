"""신호가 어느 스레드로 가든 pimon 이 받아 처리하는가.

프로세스에 보낸 신호는 커널이 아무 스레드에나 넘길 수 있다. 파이썬 신호 처리기는 주 스레드에서만
돌므로, web 의 요청 스레드가 신호를 가져가면 Event.wait 로 잠든 주 스레드는 깨지 않고 대기 시간
(interval_s) 이 끝나야 처리기를 돈다. 요청 직후 SIGHUP 을 연달아 보내고 SIGTERM 을 보내면
interval_s 동안 끝나지 않던 버그 (tools/hup_stress.py 로 30회 중 29회).
"""

import signal
import time
import urllib.request
from pathlib import Path

from fake.make_fake import make
from tests.test_main import free_port, pimon, state  # noqa: F401

SIGS = {signal.SIGHUP, signal.SIGINT, signal.SIGTERM}


def write_cfg(tmp_path, port, state):
    p = tmp_path / "pimon.toml"
    p.write_text(f'interval_s = 30\n[http]\nbind = "127.0.0.1"\nport = {port}\n'
                 f'[led]\nstate_path = "{state}"\n')
    return p


def wait_web(port, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=1).close()
            return True
        except OSError:
            time.sleep(0.05)
    return False


def blocked(pid):
    """{tid: 막힌 신호 집합}"""
    out = {}
    for t in Path(f"/proc/{pid}/task").iterdir():
        for line in (t / "status").read_text().splitlines():
            if line.startswith("SigBlk:"):
                mask = int(line.split()[1], 16)
                out[t.name] = {s for s in SIGS if mask >> (s - 1) & 1}
    return out


def test_sighups_then_sigterm_right_after_web_request_exits_quickly(fake_pi, tmp_path, state, pimon):
    """hup_stress 와 같은 순서: web 응답 직후 SIGHUP 5번 + SIGTERM. interval 30초여도 곧 끝나야 한다."""
    make(fake_pi, temp=82)
    slow = []
    for i in range(10):
        port = free_port()
        proc = pimon(write_cfg(tmp_path, port, state))
        assert wait_web(port), "web 이 뜨지 않음"
        for _ in range(5):
            proc.send_signal(signal.SIGHUP)
        proc.send_signal(signal.SIGTERM)
        try:
            _, err = proc.communicate(timeout=5)
        except Exception:
            slow.append(i)
            proc.kill()
            proc.communicate()
            continue
        assert proc.returncode == 0, err
        assert "끝냄" in err and "Traceback" not in err
    assert slow == [], f"10회 중 {len(slow)}회 SIGTERM 뒤 5초 안에 끝나지 않음"


def test_every_thread_blocks_signals_so_only_sigwait_takes_them(fake_pi, tmp_path, state, pimon):
    """신호를 받을 수 있는 스레드는 sigwait 로 기다리는 전용 스레드 하나뿐이어야 한다.

    (커널은 sigwait 로 잠든 동안 기다리는 신호를 그 스레드의 SigBlk 에서 잠시 뺀다.)"""
    make(fake_pi, temp=82)
    port = free_port()
    proc = pimon(write_cfg(tmp_path, port, state))
    assert wait_web(port), "web 이 뜨지 않음"
    masks = blocked(proc.pid)
    assert len(masks) >= 3                                  # 주 스레드 + web + 신호 스레드
    open_ = [tid for tid, m in masks.items() if m != SIGS]
    assert len(open_) == 1, masks
    proc.send_signal(signal.SIGTERM)
    _, err = proc.communicate(timeout=5)
    assert proc.returncode == 0, err
