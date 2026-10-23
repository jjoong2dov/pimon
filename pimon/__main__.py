"""명령줄 진입점.

  python3 -m pimon once [--config pimon.toml]         한 번 수집해 JSON 으로 찍기 (종료 코드 0 정상, 1 주의·모름, 2 경고)
  python3 -m pimon run [--config pimon.toml]          계속 돌며 LED·HTTP 로 보여 주기 (SIGHUP: 설정 다시 읽기)
  python3 -m pimon check-config --config pimon.toml   설정 파일만 검사
  python3 -m pimon health [--config pimon.toml]       돌고 있는 pimon 의 /status 확인 (배포 뒤 확인용)
  python3 -m pimon restore-led [--config pimon.toml]  죽은 pimon 이 남긴 LED 상태 되돌리기 (systemd ExecStopPost)
"""

import argparse
import json
import os
import select
import signal
import socket
import sys
import threading
import urllib.request
from datetime import datetime

from . import collect, config, judge, led as ledmod, server

EXIT_CODES = {"ok": 0, "unknown": 1, "warn": 1, "crit": 2}


def snapshot(cfg):
    checks = collect.collect(cfg)
    level = judge.judge(checks, cfg["limits"])
    return {
        "time": datetime.now().astimezone().isoformat(timespec="seconds"),
        "host": socket.gethostname(),
        "level": level,
        "checks": checks,
    }


def log(msg):
    print(msg, flush=True)


def cmd_once(cfg):
    snap = snapshot(cfg)
    print(json.dumps(snap, ensure_ascii=False, indent=2))
    return EXIT_CODES[snap["level"]]


def _restore_led(led):
    try:
        led.restore()
        log(f"LED 원래대로 ({led.original['trigger']})")
    except OSError as e:
        log(f"LED 되돌리기 실패: {e}")


class Signals:
    """시그널은 표시만 해 두고, 기다리던 고리를 깨운다.

    커널은 프로세스에 온 시그널을 아무 스레드(예: HTTP 요청 스레드)에나 넘길 수 있고,
    파이썬 처리기는 메인 스레드에서만 돈다. 메인 스레드가 Event.wait(interval) 로 잠들어
    있으면 처리기는 대기가 끝날 때에야 돈다. set_wakeup_fd 는 어느 스레드가 받든 파이프에
    1바이트를 써 주므로, select 로 기다리는 메인 스레드가 곧바로 깨어 처리기를 돌린다.
    처리기 안에서는 잠금을 쓰지 않는다(파이썬 문서가 경고하는 교착을 피하려고).
    """

    def __init__(self):
        self.stop = False      # 한 번 켜지면 끄지 않는다
        self.hups = 0          # 처리기는 늘리기만 하고, 고리는 본 값과 비교한다
        self.seen_hups = 0
        self._r, w = os.pipe()
        os.set_blocking(self._r, False)
        os.set_blocking(w, False)
        signal.set_wakeup_fd(w)
        signal.signal(signal.SIGTERM, self._on_stop)
        signal.signal(signal.SIGINT, self._on_stop)
        signal.signal(signal.SIGHUP, self._on_hup)

    def _on_stop(self, *_):
        self.stop = True

    def _on_hup(self, *_):
        self.hups += 1

    def take_reload(self):
        """지난번 뒤로 SIGHUP 이 왔으면 True. 사이에 몇 번 왔어도 한 번만 다시 읽으면 된다."""
        hups = self.hups
        if hups == self.seen_hups:
            return False
        self.seen_hups = hups
        return True

    def wait(self, seconds):
        """seconds 동안 또는 시그널이 올 때까지 기다린다."""
        if self.stop or self.hups != self.seen_hups:
            return
        select.select([self._r], [], [], seconds)
        try:
            while os.read(self._r, 512):
                pass
        except BlockingIOError:
            pass


def cmd_run(cfg, config_path):
    sig = Signals()

    led = None
    if cfg["led"]["enabled"]:
        try:
            led = ledmod.Led(cfg["root"], cfg["led"]["name"], cfg["state_dir"])
            how = "지난번에 남긴 기록" if led.recovered else "지금 상태"
            log(f"LED {led.path.name} 사용 (원래 trigger: {led.original['trigger']}, {how})")
        except (OSError, ValueError) as e:
            log(f"LED 를 쓰지 않음: {e}")

    state = server.State()
    host, port = config.listen_addr(cfg)
    httpd = server.start(state, host, port)
    log(f"http://{host}:{port}/ 에서 대기, {cfg['interval']:g}초마다 수집")

    last = None
    try:
        while not sig.stop:
            if sig.take_reload():
                _reload(cfg, config_path)
            snap = snapshot(cfg)
            state.set(snap)
            if snap["level"] != last:
                bad = [f"{k}={v['level']}({v['detail']})" for k, v in snap["checks"].items()
                       if v["level"] != "ok"]
                log(f"상태 {last} -> {snap['level']} {' '.join(bad)}".rstrip())
                last = snap["level"]
            if led:
                try:
                    led.show(snap["level"])
                except OSError as e:
                    log(f"LED 쓰기 실패, LED 끔: {e}")
                    _restore_led(led)
                    led = None
            sig.wait(cfg["interval"])
    finally:
        httpd.shutdown()
        if led:
            _restore_led(led)
    log("끝")
    return 0


def _reload(cfg, config_path):
    try:
        new = config.load(config_path)
    except (OSError, config.ConfigError) as e:
        log(f"설정 다시 읽기 실패, 이전 설정 유지: {e}")
        return
    if (new["listen"], new["root"], new["state_dir"], new["led"]) != \
            (cfg["listen"], cfg["root"], cfg["state_dir"], cfg["led"]):
        log("listen·root·state_dir·led 바뀜은 다시 시작해야 반영됩니다")
    for key in ("interval", "limits", "iface", "disk_path", "throttle_source"):
        cfg[key] = new[key]
    log("설정 다시 읽음")


def cmd_health(cfg):
    host, port = config.listen_addr(cfg)
    if host == "0.0.0.0":
        host = "127.0.0.1"
    try:
        with urllib.request.urlopen(f"http://{host}:{port}/status", timeout=5) as r:
            snap = json.load(r)
    except (OSError, ValueError) as e:
        print(f"응답 없음: {e}", file=sys.stderr)
        return 1
    print(f"응답 정상: {snap['host']} {snap['level']}")
    return 0


def cmd_restore_led(cfg):
    try:
        done = ledmod.restore_saved(cfg["root"], cfg["state_dir"])
    except (OSError, ValueError) as e:
        print(f"LED 되돌리기 실패: {e}", file=sys.stderr)
        return 1
    print("LED 를 기록된 상태로 되돌림" if done else "되돌릴 기록 없음")
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="pimon", description="라즈베리 파이 상태 모니터")
    p.add_argument("command", choices=["once", "run", "check-config", "health", "restore-led"])
    p.add_argument("--config", help="설정 파일 (없으면 기본값)")
    p.add_argument("--root", help="설정의 root 를 덮어씀 (가짜 파이 폴더)")
    p.add_argument("--state-dir", help="설정의 state_dir 을 덮어씀")
    args = p.parse_args(argv)

    try:
        cfg = config.load(args.config)
    except (OSError, config.ConfigError) as e:
        print(f"설정 오류: {e}", file=sys.stderr)
        return 3
    if args.root:
        cfg["root"] = args.root
    if args.state_dir:
        cfg["state_dir"] = args.state_dir

    if args.command == "check-config":
        print("설정 정상")
        return 0
    if args.command == "once":
        return cmd_once(cfg)
    if args.command == "health":
        return cmd_health(cfg)
    if args.command == "restore-led":
        return cmd_restore_led(cfg)
    return cmd_run(cfg, args.config)


if __name__ == "__main__":
    sys.exit(main())
