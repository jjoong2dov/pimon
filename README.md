# pimon

라즈베리 파이 4 한 대에서 도는 작은 상태 모니터를 만든다.

- CPU 온도, 전원·스로틀 상태, 디스크·메모리 사용률, 유선 링크 상태를 몇 초마다 읽는다.
- 설정 파일의 기준값으로 정상·주의·경고를 판정한다.
- 보드에 달린 ACT LED 깜빡임으로 상태를 보여 주고, HTTP 로 JSON 과 간단한 웹 화면을 내놓는다.
- systemd 서비스로 돌린다.
- 파이에 따로 설치할 것이 없게 파이썬 표준 라이브러리만 쓴다. 파이 OS 의 python3 는 3.13 이다.

파이 없이 PC 에서 만들고 시험할 수 있게 가짜 파이 폴더를 만드는 도구가 있다.

    python3 fake/make_fake.py fake/pi --fresh      # 정상 상태로 만들기
    python3 fake/make_fake.py fake/pi --temp 82    # 값 바꾸기

가짜 파이 폴더 안에는 실제 파이와 같은 경로에 파일이 있다
(sys/class/thermal/thermal_zone0/temp, sys/class/leds/ACT/trigger, proc/meminfo 등).

## 실행

    python3 -m pimon --root fake/pi --config my.toml   # 주기 실행, http://127.0.0.1:8080/ (Ctrl-C 로 끝냄)
    python3 -m pimon --root fake/pi --once             # 한 번 수집·판정해 JSON 출력
    python3 -m pimon --restore-led                     # 기록 파일대로 ACT LED 되돌리기 (systemd ExecStopPost)

설정 예시는 `deploy/pimon.toml`, systemd 유닛은 `deploy/pimon.service`.
PC 에서 주기 실행할 때는 `[led] state_path` 를 쓸 수 있는 곳(예: `/tmp/pimon/led.json`)으로 바꾼다
(기본 `/run/pimon/led.json` 은 보통 root 만 쓸 수 있다).

시험: `.venv/bin/python -m pytest -q`
