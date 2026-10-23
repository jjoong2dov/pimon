"""명령줄 진입점.

  python3 -m pimon once [--config pimon.toml]         한 번 수집해 JSON 으로 찍기 (종료 코드 0 정상, 1 주의·모름, 2 경고)
  python3 -m pimon run [--config pimon.toml]          계속 돌며 LED·HTTP 로 보여 주기 (SIGHUP: 설정 다시 읽기)
  python3 -m pimon check-config --config pimon.toml   설정 파일만 검사
"""

import argparse
import json
import signal
import socket
import sys
import threading
from datetime import datetime

from . import collect, config, judge, server
from .led import Led

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


def cmd_run(cfg, config_path):
    stop = threading.Event()
    reload = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    signal.signal(signal.SIGHUP, lambda *_: (reload.set(), stop.set()))

    led = None
    if cfg["led"]["enabled"]:
        try:
            led = Led(cfg["root"], cfg["led"]["name"])
            log(f"LED {led.path.name} 사용 (원래 trigger: {led.original})")
        except OSError as e:
            log(f"LED 를 쓰지 않음: {e}")

    state = server.State()
    host, port = config.listen_addr(cfg)
    httpd = server.start(state, host, port)
    log(f"http://{host}:{port}/ 에서 대기, {cfg['interval']:g}초마다 수집")

    last = None
    try:
        while True:
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
                    led = None
            stop.wait(cfg["interval"])
            if reload.is_set():
                reload.clear()
                stop.clear()
                try:
                    new = config.load(config_path)
                except (OSError, config.ConfigError) as e:
                    log(f"설정 다시 읽기 실패, 이전 설정 유지: {e}")
                    continue
                if (new["listen"], new["root"], new["led"]) != (cfg["listen"], cfg["root"], cfg["led"]):
                    log("listen·root·led 바뀜은 다시 시작해야 반영됩니다")
                cfg["interval"], cfg["limits"] = new["interval"], new["limits"]
                cfg["iface"], cfg["disk_path"] = new["iface"], new["disk_path"]
                cfg["throttle_source"] = new["throttle_source"]
                log("설정 다시 읽음")
                continue
            if stop.is_set():
                break
    finally:
        httpd.shutdown()
        if led:
            led.restore()
            log(f"LED 원래대로 ({led.original})")
    log("끝")
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="pimon", description="라즈베리 파이 상태 모니터")
    p.add_argument("command", choices=["once", "run", "check-config"])
    p.add_argument("--config", help="설정 파일 (없으면 기본값)")
    p.add_argument("--root", help="설정의 root 를 덮어씀 (가짜 파이 폴더)")
    args = p.parse_args(argv)

    try:
        cfg = config.load(args.config)
    except (OSError, config.ConfigError) as e:
        print(f"설정 오류: {e}", file=sys.stderr)
        return 3
    if args.root:
        cfg["root"] = args.root

    if args.command == "check-config":
        print("설정 정상")
        return 0
    if args.command == "once":
        return cmd_once(cfg)
    return cmd_run(cfg, args.config)


if __name__ == "__main__":
    sys.exit(main())
