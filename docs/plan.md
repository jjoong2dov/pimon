# pimon 설계 계획

코드를 쓰기 전에 정한 구조와 결정 사항. 바뀌면 이 문서를 먼저 고친다.

## 전제
- 파이썬 표준 라이브러리만. 대상은 파이 OS python3 3.13, 개발 PC 는 3.12 → **3.12 에서도 도는 코드**로 쓴다 (3.13 전용 기능 금지).
- 모든 읽기·쓰기는 `root` 아래에서 한다 (실제 파이는 `/`, 시험은 가짜 파이 폴더).
- `/sys` 에 쓰는 곳은 `led.py` 하나뿐.
- 수집 함수는 예외를 밖으로 내지 않는다. 읽지 못하면 오류를 값으로 돌려주고 판정은 `unknown`.
- 개발용 pytest 는 PC 의 `.venv` 에만 넣는다. 파이에는 아무것도 설치하지 않는다.

## 1. 파일 구성

```
pimon/
  __init__.py
  __main__.py   실행 입구: python3 -m pimon [--root /] [--config /etc/pimon/pimon.toml] [--once]
  config.py     기본값 + TOML 읽기(tomllib) + 값 검사. 파일이 없으면 기본값
  collect.py    항목별 수집 함수. root 를 받아 그 아래에서 읽음
  judge.py      수집값 + 설정 → level / reason. 파일을 건드리지 않는 순수 함수
  monitor.py    Monitor: tick() = 수집→판정→현재 상태 저장(잠금), run() = 주기 반복, stop()
  led.py        ACT LED: 시작 때 원래 trigger·brightness 저장, 상태에 따라 깜빡임, 끝날 때 복구
  web.py        ThreadingHTTPServer: GET /api/status (JSON), GET / (page.html)
  page.html     간단한 화면. /api/status 의 interval_s 마다 읽어 표로 보여 줌 (최소 1초)
deploy/
  pimon.service systemd 유닛 (root 로 실행, 보안 옵션으로 좁힘)
  pimon.toml    예시 설정 (기본값과 같은 내용)
tests/
  conftest.py   fake_pi 픽스처
  test_config.py, test_collect.py, test_judge.py, test_monitor.py, test_led.py, test_web.py
```

이력 저장과 깜빡임 방지(N번 연속일 때만 판정 바꾸기)는 처음에는 넣지 않는다. 현재 상태만 내놓는다.

## 2. 판정 이름과 순서

| 이름 | 뜻 |
|---|---|
| `ok` | 정상 |
| `unknown` | 모름 (읽기 실패) |
| `warn` | 주의 |
| `crit` | 경고 |

전체 판정(`overall`)은 가장 나쁜 것: `ok < unknown < warn < crit`.

## 3. 데이터 모양

### 수집 함수 반환 (항목 하나)
```json
{"value": {...}, "error": null}
{"value": null, "error": "FileNotFoundError: .../thermal_zone0/temp"}
```

### `/api/status` (판정까지 붙인 것)
```json
{
  "time": "2026-10-23T10:15:30+09:00",
  "overall": "warn",
  "items": {
    "temp":     {"value": {"c": 48.3},
                 "error": null, "level": "ok", "reason": "48.3°C"},
    "throttle": {"value": {"raw": "0x50000", "now": [], "past": ["under_voltage", "throttled"]},
                 "error": null, "level": "warn", "reason": "부팅 뒤 저전압·스로틀 있었음"},
    "memory":   {"value": {"total_mb": 3884, "avail_mb": 3200, "used_pct": 17.6},
                 "error": null, "level": "ok", "reason": "17.6%"},
    "disk":     {"value": [{"path": "/", "total_gb": 29.1, "used_pct": 41.0}],
                 "error": null, "level": "ok", "reason": "/ 41.0%"},
    "link":     {"value": {"iface": "eth0", "up": true, "carrier": true, "speed_mbps": 1000},
                 "error": null, "level": "ok", "reason": "up"},
    "system":   {"value": {"load1": 0.12, "load5": 0.12, "load15": 0.12, "uptime_s": 3600},
                 "error": null, "level": "ok", "reason": ""}
  },
  "interval_s": 5
}
```
`interval_s` 는 web 이 설정값을 덧붙인 것이다 (수집 전 503 응답 `{"error": ..., "interval_s": 5}` 에도 들어감). 화면은 이 값으로 다음 갱신을 잡는다.

### 항목별 판정 규칙
| 항목 | 규칙 |
|---|---|
| temp | `≥ crit_c` → crit, `≥ warn_c` → warn, 그 밖 ok |
| throttle | 지금 비트(0~3) 하나라도 → crit, 과거 비트(16~19)만 → warn (재부팅 전까지 계속 warn), 없으면 ok |
| memory | 사용률 = (Total − Available) / Total. `≥ crit_used_pct` → crit, `≥ warn_used_pct` → warn |
| disk | 설정한 경로마다 사용률을 보고 가장 나쁜 것. 기준은 memory 와 같은 방식. 경로 하나라도 못 읽으면 unknown |
| link | operstate 가 `up` 이 아니면 crit. 속도는 **값만 보여 주고 판정하지 않음** |
| system | 부하·가동시간은 **보여 주기만**. 읽혔으면 항상 ok |

- 어느 항목이든 `error` 가 있으면 `unknown`.
- throttle 비트 이름: 0 `under_voltage`, 1 `freq_capped`, 2 `throttled`, 3 `soft_temp_limit` (16~19 는 같은 이름으로 `past` 에 넣음).

## 4. 설정 파일

위치는 `/etc/pimon/pimon.toml`. 파일이 없으면 아래 기본값으로 돈다.

```toml
interval_s = 5

[http]
bind = "127.0.0.1"     # 밖에서 보려면 "0.0.0.0" 등으로 바꿈. 인증 없음
port = 8080

[led]
enabled = true

[temp]                 # 파이 4 펌웨어는 80°C 부터 클럭을 내림
warn_c = 70
crit_c = 80

[memory]
warn_used_pct = 80
crit_used_pct = 90

[disk]
paths = ["/"]          # root 아래로 이어 붙여 os.statvfs
warn_used_pct = 80
crit_used_pct = 90

[link]
iface = "eth0"
```

- 모르는 키, 형식이 틀린 값, `warn ≥ crit` 같은 모순은 시작할 때 오류로 멈춘다.
- `--root` 는 설정 파일이 아니라 실행 인자로만 받는다 (기본 `/`).

## 5. LED 동작

| 전체 판정 | LED |
|---|---|
| ok | 원래 동작으로 둠 (시작 때 저장한 trigger, 보통 `mmc0`) |
| unknown | 원래 동작으로 둠 (깜빡임은 warn·crit 에만 쓴다는 결정에 따름) |
| warn | 느리게 깜빡임 (예: 1초 켜짐 / 1초 꺼짐) |
| crit | 빠르게 깜빡임 (예: 0.15초 켜짐 / 0.15초 꺼짐) |

- 깜빡임은 trigger 를 `none` 으로 두고 스레드가 `brightness` 에 0/1 을 쓰는 방식. (가짜 파이에는 timer trigger 의 `delay_on/off` 파일이 없어서 이 방식이 시험하기 쉽다.)
- 판정이 ok 로 돌아오거나 프로그램이 끝나면(SIGTERM·SIGINT 포함) 원래 trigger 와 brightness 를 다시 쓴다.
- `led.enabled = false` 면 LED 를 전혀 건드리지 않는다.
- 복구는 trigger 를 먼저 쓰고, 원래 trigger 가 `none` 일 때만 brightness 를 쓴다 (커널은 brightness 0 쓰기에 trigger 를 떼어 버림).
- LED 를 읽지 못하면(파일 없음, 형식 오류) 시작 때 `LedError`, 아무것도 쓰지 않는다.
- SIGTERM·SIGINT: 처리기가 감시 루프를 멈추고, 끝나면서 web 을 닫은 뒤 LED 를 복구한다. 종료 코드 0.
- **강제 종료 대비 기록 파일** (결정 13): 시작 때 원래 trigger·brightness 를 `[led] state_path`(기본 `/run/pimon/led.json`, `--root` 와 무관한 실제 경로)에 원자적으로 적는다.
  - 다시 시작할 때 기록이 있으면 지금 LED 값(`none` 일 수 있음) 대신 기록을 원래 값으로 쓰고, LED 가 기록과 다르면 곧바로 되돌린다.
  - 정상 복구에 성공하면 기록을 지운다. 기록을 쓸 수 없으면 LED 를 건드리지 않고(LedError) LED 없이 감시를 계속한다.
  - `python3 -m pimon --restore-led`: 기록대로 되돌리고 지운다. 기록이 없으면 아무것도 안 하고 0, 기록이 깨졌으면 아무것도 쓰지 않고 1. systemd `ExecStopPost` 에서 부른다.
  - 남는 한계: 기록 파일이 깨졌는데 그때 LED 가 `none` 이면 `none` 을 원래 값으로 다시 적는다 (경고 로그). 설정 파일이 깨지면 `--restore-led` 도 2 로 멈춘다.

## 6. systemd

- root 로 실행. 보안 옵션으로 좁힌다: `NoNewPrivileges=yes`, `ProtectSystem=strict`, `ProtectHome=yes`, `PrivateTmp=yes`, `ReadWritePaths=` 에 ACT LED 폴더만.
- `ProtectKernelTunables=yes` 는 `/sys` 를 읽기 전용으로 만들어 LED 쓰기를 막으므로 쓰지 않거나 LED 경로를 따로 열어야 한다. 실제 파이에서 확인 필요 (배포는 사람 확인 후).
- `Restart=on-failure`. 끝날 때 SIGTERM 으로 LED 복구, `ExecStopPost=... --restore-led` 로 강제 종료 뒤 복구.
- `RuntimeDirectory=pimon` + **`RuntimeDirectoryPreserve=yes`** (기본값이면 재시작 때 `/run/pimon` 이 지워져 기록이 사라진다).
- web 포트를 열 수 없으면 LED 를 복구하고 종료 코드 1 (systemd 가 다시 띄움).
- 실제 LED 경로(`readlink -f /sys/class/leds/ACT`)와 `ReadWritePaths` 동작은 파이에서 확인 필요.

## 7. 가짜 파이로 시험하기

- `fake_pi` 픽스처: `make(tmp_path / "pi", fresh=True)` 로 시험마다 새 폴더.
- 값 바꾸기: `make(root, temp=82)`, `make(root, throttled="0x50005")`, `make(root, link="down")` 등.
- 읽기 실패: 시험 안에서 파일을 지우거나 깨진 내용을 써서 `error` 와 `unknown` 을 확인.
- 디스크: 가짜 폴더에 없으므로 `collect_disk(root, paths, statvfs=os.statvfs)` 로 함수를 바꿔 끼워 시험. 없는 경로는 오류 값.
- judge: dict 만으로 경계값(예: 69.9 / 70 / 79.9 / 80) 표 시험.
- LED: 가짜 폴더의 `trigger`·`brightness` 에 쓴 내용, stop() 뒤 원래 값으로 돌아왔는지 확인. 실행 전후 가짜 폴더 전체를 비교해 LED 말고는 바뀌지 않았는지 확인.
- web: 포트 0 으로 스레드에 띄우고 `urllib` 로 `/api/status` 와 `/` 확인.
- monitor: `tick()` 을 직접 불러 시간 대기 없이 시험.
- 손 확인: `python3 -m pimon --root fake/pi` 를 띄우고 다른 창에서 `python3 fake/make_fake.py fake/pi --temp 82` 로 바꾼 뒤 `http://127.0.0.1:8080/` 확인.

## 8. 만드는 순서

각 단계는 시험 먼저 → `.venv/bin/python -m pytest -q` 통과 → 짧게 보고.

0. `python3 -m venv .venv` 후 pytest 설치 (PC 전용)
1. config.py
2. collect.py
3. judge.py
4. monitor.tick + `__main__ --once` (JSON 한 번 출력) ← 처음 써 볼 수 있는 지점
5. web.py + page.html
6. led.py (복구 포함)
7. run 루프, 신호 처리(LED 복구), deploy/pimon.service, deploy/pimon.toml, README 갱신
8. 실제 파이 배포 — 사람 확인 후

## 결정 기록 (2026-10-23)
1. 판정 이름 ok / unknown / warn / crit, 순서 ok < unknown < warn < crit.
2. 기준값 기본: 온도 70/80°C, 메모리·디스크 80/90%.
3. 과거 스로틀 비트는 재부팅 전까지 warn.
4. 링크 down → crit. 속도는 값만. 인터페이스 이름은 설정.
5. 부하·가동시간은 보여 주기만.
6. 디스크는 `/` 만, 경로는 설정.
7. HTTP 기본 127.0.0.1:8080, 설정으로 변경 가능, 인증 없음.
8. LED 는 pimon 이 가져감. ok 면 원래 동작(mmc0), warn 느리게, crit 빠르게.
9. 우선 root 로 실행, systemd 보안 옵션으로 좁힘. 나중에 다시 검토.
10. 개발 PC `.venv` 에 pytest 설치 허용. 3.12 호환 코드.
11. 설정 파일 `/etc/pimon/pimon.toml`.
12. 이력 저장·깜빡임 방지는 처음엔 뺌.
13. LED 강제 종료 복구는 기록 파일 방식(제안 1): `/run/pimon/led.json`(설정 가능), 시작 때 기록 우선, `--restore-led` 를 ExecStopPost 에서.
