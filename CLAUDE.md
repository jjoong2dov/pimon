# pimon — Claude Code 작업 규칙

## 이 저장소
- 라즈베리 파이 4 한 대에서 도는 상태 모니터 `pimon`. 파이썬 표준 라이브러리만 쓴다(파이에 설치할 것이 없게).
- 수집 `pimon/collect.py` → 판정 `pimon/judge.py` → 표시 `pimon/led.py`(ACT LED), `pimon/server.py`(HTTP).
- 파이 없이 돌릴 때는 가짜 파이 폴더(`fake/make_fake.py`)를 `root` 로 준다. 실제 파이에서는 `root = "/"`.

## 절대 하지 말 것
- 수집 함수에서 예외를 밖으로 내보내지 않는다. 읽지 못하면 `{"error": ...}` 를 돌려주고 판정은 unknown 이 된다.
- 바깥 패키지(pip)를 넣지 않는다. 꼭 필요하면 이유를 먼저 보고한다.
- 실제 파이의 `/sys` 에 쓰는 코드는 LED 말고는 만들지 않는다. LED 는 끝낼 때 원래 trigger 로 되돌린다.
- 시험을 통과시키려고 판정 기준(limits 기본값)을 바꾸지 않는다.
- 배포 스크립트(`deploy/`)를 사람 확인 없이 실제 파이에 돌리지 않는다.

## 작업 순서
1. 바꿀 내용을 짧게 계획하고 확인받는다.
2. 시험을 먼저 쓰거나 고친다. 가짜 파이로 재현할 수 있는 것은 가짜 파이 시험으로 만든다.
3. `.venv/bin/python -m pytest -q` 를 통과시킨다.
4. 다른 AI(Codex)에게 diff 검토를 받는다. 추측으로 넣은 코드, 쓰지 않는 코드를 지운다.
5. 결과는 한국어로 짧게 보고한다: 무엇을 바꿨고, 무엇을 돌렸고, 결과가 어땠는지.

## 명령
- 단위 시험: `.venv/bin/python -m pytest -q`
- 가짜 파이: `python3 fake/make_fake.py fake/pi --fresh`, 값 바꾸기 `--temp 82 --link down`
- 한 번 수집: `python3 -m pimon once --root fake/pi`
- 계속 돌리기: `python3 -m pimon run --config fake/pc.toml`
- 배포(사람이 실행): `sh deploy/deploy.sh pi@파이주소`, 되돌리기 `sh deploy/rollback.sh pi@파이주소`
