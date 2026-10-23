"""pimon.service 의 ExecStart·ExecStopPost 가 지금 pimon 명령행과 맞는지 본다.

  python3 check_service.py deploy/pimon.service deploy/pimon.toml

각 명령행을 pimon.__main__.main() 에 그대로 넣되, 실제 일(serve·restore_led)과 설정 읽기는
가로채서 아무것도 실행하지 않는다. 그래서 argparse 의 옵션 검사와 main() 안의 조합 검사
(--state-path 는 --restore-led 와 함께 등)를 지금 코드 그대로 거친다.

  ExecStart    : 주기 실행(serve)으로 가야 한다. --config 는 이 toml 을 읽은 것으로 친다.
  ExecStopPost : restore_led 로 가야 하고, 그 기록 경로가 toml 의 led.state_path 와 같아야 한다.

항목마다 "통과|실패 <키>: <근거>" 한 줄을 찍는다. 모두 통과면 0, 아니면 1.
"""

import contextlib
import io
import shlex
import sys
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))

from pimon import __main__ as entry, config  # noqa: E402

KEYS = ("ExecStart", "ExecStopPost")


def read_exec_lines(service):
    """ExecStart·ExecStopPost 값. 같은 키가 여러 번이면 마지막 것."""
    found = {}
    for line in Path(service).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith(("#", ";")) or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() in KEYS:
            found[key.strip()] = value.strip()
    return found


def pimon_args(cmd):
    """'/usr/bin/python3 -m pimon ...' 에서 pimon 뒤 인자. pimon 명령이 아니면 None."""
    argv = shlex.split(cmd.lstrip("-@:+!"))      # systemd 의 실행 접두사는 떼어 낸다
    if len(argv) < 3 or argv[1:3] != ["-m", "pimon"]:
        return None
    return argv[3:]


def dry_run(args, cfg):
    """main(args) 를 돌리되 일은 가로챈다. (불린 것, 인자) 또는 ('error', 메시지)."""
    calls = []
    err = io.StringIO()
    with mock.patch.object(entry.config, "load", lambda path=None: cfg), \
         mock.patch.object(entry, "serve", lambda root, c, path: calls.append(("serve", None)) or 0), \
         mock.patch.object(entry, "restore_led",
                           lambda root, path: calls.append(("restore_led", path)) or 0), \
         mock.patch.object(entry, "_setup_logging", lambda: None), \
         contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
        try:
            entry.main(args)
        except SystemExit as e:
            if e.code:
                lines = err.getvalue().strip().splitlines()
                return "error", lines[-1] if lines else f"종료 코드 {e.code}"
    if not calls:
        return "error", "serve·restore_led 어느 쪽도 불리지 않음 (--once 등)"
    return calls[0]


def check(key, cmd, cfg):
    args = pimon_args(cmd)
    if args is None:
        return False, f"python3 -m pimon 명령이 아님: {cmd}"
    shown = " ".join(args)
    what, detail = dry_run(args, cfg)
    if what == "error":
        return False, f"{shown} → {detail}"
    if key == "ExecStart":
        if what != "serve":
            return False, f"{shown} → 주기 실행이 아니라 {what}"
        return True, f"{shown} → 주기 실행(serve)"
    if what != "restore_led":
        return False, f"{shown} → LED 복구가 아니라 {what}"
    state_path = cfg["led"]["state_path"]
    if detail != state_path:
        return False, f"--state-path {detail} ≠ pimon.toml led.state_path {state_path}"
    return True, f"{shown} → restore_led({detail}), led.state_path 와 같음"


def main(argv):
    if len(argv) != 2:
        print("쓰는 법: check_service.py <pimon.service> <pimon.toml>", file=sys.stderr)
        return 2
    service, toml = argv
    try:
        cfg = config.load(toml)
    except config.ConfigError as e:
        print(f"실패 설정: {e}")
        return 1
    lines = read_exec_lines(service)
    ok = True
    for key in KEYS:
        if key not in lines:
            good, why = False, f"{service} 에 {key}= 줄이 없음"
        else:
            good, why = check(key, lines[key], cfg)
        ok &= good
        print(f"{'통과' if good else '실패'} {key}: {why}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
