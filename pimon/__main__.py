"""실행 입구: python3 -m pimon [--root /] [--config /etc/pimon/pimon.toml] [--once]"""

import argparse
import json
import sys

from pimon import config
from pimon.monitor import Monitor


def main(argv=None):
    p = argparse.ArgumentParser(prog="pimon", description="라즈베리 파이 상태 모니터")
    p.add_argument("--root", default="/", help="이 경로 아래의 sys·proc 을 읽는다 (시험: fake/pi)")
    p.add_argument("--config", default=config.DEFAULT_PATH, help="설정 파일 (없으면 기본값)")
    p.add_argument("--once", action="store_true", help="한 번 수집·판정해 JSON 으로 출력하고 끝낸다")
    a = p.parse_args(argv)

    try:
        cfg = config.load(a.config)
    except config.ConfigError as e:
        print(f"pimon: 설정 오류: {e}", file=sys.stderr)
        return 2

    if not a.once:
        print("pimon: 주기 실행은 아직 없음. --once 로 돌릴 것", file=sys.stderr)
        return 2

    status = Monitor(a.root, cfg).tick()
    # allow_nan=False: NaN 이 섞이면 조용히 잘못된 JSON 을 내지 않고 바로 드러나게 한다
    print(json.dumps(status, ensure_ascii=False, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
