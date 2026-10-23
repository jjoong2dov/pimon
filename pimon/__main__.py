"""실행 입구.

  python3 -m pimon [--root /] [--config /etc/pimon/pimon.toml]   주기 실행 (SIGTERM·SIGINT 로 끝냄)
  python3 -m pimon ... --once          한 번 수집·판정해 JSON 으로 출력
  python3 -m pimon ... --restore-led   기록 파일대로 LED 를 되돌림 (systemd ExecStopPost)
"""

import argparse
import contextlib
import json
import logging
import signal
import sys
import threading

from pimon import config, web
from pimon.led import Led, LedError, StateError, restore_from_state
from pimon.monitor import Monitor

log = logging.getLogger("pimon")


def main(argv=None):
    p = argparse.ArgumentParser(prog="pimon", description="라즈베리 파이 상태 모니터")
    p.add_argument("--root", default="/", help="이 경로 아래의 sys·proc 을 읽는다 (시험: fake/pi)")
    p.add_argument("--config", default=config.DEFAULT_PATH, help="설정 파일 (없으면 기본값)")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true", help="한 번 수집·판정해 JSON 으로 출력하고 끝낸다")
    mode.add_argument("--restore-led", action="store_true",
                      help="led.state_path 의 기록대로 LED 를 되돌리고 기록을 지운다 (기록이 없으면 아무것도 안 함)")
    a = p.parse_args(argv)

    try:
        cfg = config.load(a.config)
    except config.ConfigError as e:
        print(f"pimon: 설정 오류: {e}", file=sys.stderr)
        return 2

    if a.once:
        status = Monitor(a.root, cfg).tick()
        # allow_nan=False: NaN 이 섞이면 조용히 잘못된 JSON 을 내지 않고 바로 드러나게 한다
        print(json.dumps(status, ensure_ascii=False, indent=2, allow_nan=False))
        return 0

    _setup_logging()
    if a.restore_led:
        return restore_led(a.root, cfg)
    return serve(a.root, cfg)


def _setup_logging():
    root = logging.getLogger()
    if not root.handlers:                      # journald 가 시각을 붙이므로 시각은 뺀다
        logging.basicConfig(level=logging.INFO, stream=sys.stderr,
                            format="pimon: %(levelname)s %(message)s")


def restore_led(root, cfg):
    """기록이 없으면 0 (정상 종료 뒤에도 ExecStopPost 로 불림). 기록이 깨졌거나 되돌리지 못하면 1."""
    path = cfg["led"]["state_path"]
    try:
        done = restore_from_state(root, path)
    except StateError as e:
        log.error("%s. 아무것도 쓰지 않음", e)
        return 1
    except LedError as e:
        log.error("%s", e)
        return 1
    log.info("LED 를 기록대로 되돌림" if done else "LED 기록 없음. 할 일 없음")
    return 0


def _start_led(root, cfg):
    """led.enabled 면 Led 를 시작해 돌려준다. 꺼져 있거나 시작에 실패하면 None (LED 없이 감시는 계속)."""
    if not cfg["led"]["enabled"]:
        return None
    led = Led(root, cfg["led"]["state_path"])
    try:
        led.start()
    except LedError as e:
        log.error("%s. LED 없이 계속함", e)
        return None
    return led


class StartError(Exception):
    """감시를 시작할 수 없음 (포트 사용 중 등). serve() 가 LED 를 복구한 뒤 종료 코드 1."""


def _start_extras(monitor, cfg, stack):
    """감시 루프와 함께 도는 것들을 띄우는 자리. 끝낼 일은 stack.callback 으로 등록한다.

    stack 은 들어간 반대 순서로 닫히고, LED 복구(led.stop)는 이보다 먼저 등록되어 있으므로
    여기서 등록한 것이 먼저 정리된 뒤 LED 가 복구된다.
    """
    bind, port = cfg["http"]["bind"], cfg["http"]["port"]
    try:
        srv = web.make_server(monitor.status, bind, port)
    except OSError as e:
        raise StartError(f"web {bind}:{port} 를 열 수 없음: {e}") from None
    stack.callback(srv.server_close)           # 나중에 불림
    t = threading.Thread(target=srv.serve_forever, name="pimon-web", daemon=True)
    t.start()
    stack.callback(srv.shutdown)               # 먼저 불림: serve_forever 를 멈춤
    host, real_port = srv.server_address[:2]
    log.info("web: http://%s:%s/", host, real_port)


def serve(root, cfg):
    monitor = Monitor(root, cfg)

    def on_signal(signum, frame):
        # 신호 처리기에서 Event.set 을 바로 부르면, 주 스레드가 Event.wait 안에서 같은 잠금을
        # 쥔 순간에 걸릴 때 교착될 수 있다. 다른 스레드에서 세운다.
        threading.Thread(target=monitor.stop, name="pimon-signal", daemon=True).start()

    old = {s: signal.signal(s, on_signal) for s in (signal.SIGTERM, signal.SIGINT)}
    try:
        with contextlib.ExitStack() as stack:
            led = _start_led(root, cfg)
            if led is not None:
                stack.callback(led.stop)
            _start_extras(monitor, cfg, stack)

            def on_status(status):
                if led is not None:
                    led.show(status["overall"])

            log.info("시작: root=%s interval=%ss LED=%s", root, cfg["interval_s"],
                     "켜짐" if led is not None else "꺼짐")
            monitor.run(on_status=on_status)
            log.info("끝냄")
    except StartError as e:
        log.error("%s", e)
        return 1
    finally:
        for s, h in old.items():
            signal.signal(s, h)
    return 0


if __name__ == "__main__":
    sys.exit(main())
