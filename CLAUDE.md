# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 프로젝트

pimon: 라즈베리 파이 4 한 대에서 도는 작은 상태 모니터. 아직 초기 단계라 저장소에는 README 와 가짜 파이 도구(`fake/make_fake.py`)만 있다.

목표 기능 (README 기준):
- CPU 온도, 전원·스로틀 상태, 디스크·메모리 사용률, 유선 링크 상태를 몇 초마다 읽는다.
- 설정 파일의 기준값으로 정상·주의·경고를 판정한다.
- ACT LED 깜빡임으로 상태를 보여 주고, HTTP 로 JSON 과 간단한 웹 화면을 내놓는다.
- systemd 서비스로 돌린다.

## 제약

- **파이썬 표준 라이브러리만 쓴다.** 파이에 따로 설치할 것이 없어야 한다. 대상은 파이 OS 의 python3 3.13.
- 파이 없이 PC 에서 개발·시험한다. 그러려면 pimon 코드는 `/sys`, `/proc` 같은 경로를 직접 박지 말고 **루트 경로를 받아 그 아래에서 읽고 써야** 한다 (실제 파이는 `/`, 시험은 `fake/pi`).

## 가짜 파이

```
python3 fake/make_fake.py fake/pi --fresh                       # 정상 기본값으로 새로 만들기
python3 fake/make_fake.py fake/pi --temp 82                     # 온도만 바꾸기
python3 fake/make_fake.py fake/pi --throttled 0x50005 --link down
```

`--fresh` 없이 이미 있는 폴더에 쓰면 넘긴 값만 바뀐다. 시험 코드에서는 `make(root, temp=..., ...)` 를 직접 불러도 된다.

가짜 폴더가 흉내 내는 실제 파이 경로:

| 항목 | 경로 (루트 기준) | 형식 |
|---|---|---|
| CPU 온도 | `sys/class/thermal/thermal_zone0/temp` | 밀리도 정수 (48300) |
| 스로틀 | `sys/devices/platform/soc/soc:firmware/get_throttled` | 16진 문자열, `0x` 없음 (`50005`) |
| 유선 링크 | `sys/class/net/eth0/operstate`, `carrier`, `speed` | `up`/`down`, `1`/`0`, Mb/s |
| 메모리 | `proc/meminfo` | MemTotal / MemFree / MemAvailable (kB) |
| 부하·가동시간 | `proc/loadavg`, `proc/uptime` | 커널 형식 그대로 |
| ACT LED | `sys/class/leds/ACT/trigger`, `brightness` | trigger 는 현재 값이 `[대괄호]` 로 표시된 목록 |

참고:
- 디스크 사용률은 가짜 폴더에 없다 (`os.statvfs` 등으로 읽을 것이라 별도 흉내가 필요하면 따로 마련해야 한다).
- `make()` 의 `uptime` 인자는 CLI 옵션이 없다 (`--fresh` 때 기본값 3600 으로만 쓰인다).

## 절대 하지 말 것 (사람이 덧붙임)
- 수집 함수에서 예외를 밖으로 내보내지 않는다. 읽지 못하면 오류를 값으로 돌려주고 판정은 "모름"이 된다.
- 바깥 패키지(pip)를 넣지 않는다. 꼭 필요하면 이유를 먼저 보고한다.
- 실제 파이의 `/sys` 에 쓰는 코드는 LED 말고는 만들지 않는다. LED 는 끝낼 때 원래 상태로 되돌린다.
- 시험을 통과시키려고 판정 기준값을 바꾸지 않는다.
- 실제 파이에 배포하는 명령은 사람 확인 없이 돌리지 않는다.

## 작업 순서 (사람이 덧붙임)
1. 바꿀 내용을 짧게 계획하고 확인받는다.
2. 시험을 먼저 쓴다. 가짜 파이로 재현할 수 있는 것은 가짜 파이 시험으로 만든다.
3. `.venv/bin/python -m pytest -q` 를 통과시킨다.
4. 결과는 한국어로 짧게 보고한다: 무엇을 바꿨고, 무엇을 돌렸고, 결과가 어땠는지.
