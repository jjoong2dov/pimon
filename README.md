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
