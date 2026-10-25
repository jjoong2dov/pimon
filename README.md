# pimon — 라즈베리 파이 상태 모니터

전자책 《라즈베리 파이 실습으로 익히는 Claude Code·Codex 협업 개발》(《라즈베리 파이 × AI 실전 개발실》 2권)의 실습 저장소입니다.

라즈베리 파이 4 한 대에서 도는 작은 상태 모니터입니다. 보드에 원래 있는 것만 씁니다.

- 수집: CPU 온도, 전원·스로틀 상태, 디스크·메모리 사용률, 유선 링크, 부하
- 판정: 설정 파일(TOML)의 기준값으로 정상(ok)·모름(unknown)·주의(warn)·경고(crit)
- 표시: 보드의 ACT LED 깜빡임, HTTP(`/status` JSON, `/metrics`, 웹 화면)
- 운영: systemd 서비스, `systemctl reload` 로 설정 다시 읽기, 배포·되돌리기 스크립트

파이썬 표준 라이브러리만 씁니다(Python 3.11 이상). 파이가 없어도 PC 에서 가짜 파이 폴더로 돌려 볼 수 있습니다.

## 빠른 시작 (PC, 가짜 파이)

```sh
python3 fake/make_fake.py fake/pi --fresh
python3 -m pimon once --root fake/pi --state-dir fake/state

cat > fake/pc.toml <<'EOF'
root = "fake/pi"
state_dir = "fake/state"
interval = 2
listen = "127.0.0.1:8080"
EOF
python3 -m pimon run --config fake/pc.toml     # 다른 터미널에서 curl -s 127.0.0.1:8080/status
```

값 바꾸기: `python3 fake/make_fake.py fake/pi --temp 83.5`, `--link down`, `--throttled 0x50005`

## 단위 시험

```sh
python3 -m venv .venv && .venv/bin/pip install pytest
.venv/bin/python -m pytest -q
```

## 가지와 태그

| 이름 | 내용 |
|---|---|
| `main` | 검토를 거친 완성본. 책의 1장과 부록, 9·12장 이후가 주로 씁니다 |
| `replay` | 빈 저장소에서 장 순서대로 Claude Code 에게 일을 시킨 실제 기록. 책의 3~15장이 씁니다 |
| `ch03-start` … `ch12-start`, `ch14-start`, `ch15-start` | `replay` 의 각 장 시작 상태. 13장은 `ch12-start` 에서 시작하므로 `ch13-start` 는 없습니다. `ch03-init`, `ch10-hooks`, `ch11-skill`, `ch14-bug-start` 는 장 중간 상태입니다 |
| `v0-before-review`, `v0.1-review1`, `v0.2-review2` | 완성본이 Codex 검토를 거치며 바뀐 단계. `v0.1-review1` 의 커밋 메시지는 "시그널 교착 수정"이라고 적었지만, 뒤에 다시 재 보니 교착(영영 멈춤)이 아니라 SIGHUP 직후의 SIGTERM 처리가 최대 수집 주기만큼 늦어지던 문제였습니다(시험 설정 30초에서 약 30.2초)(책 9·14장). 커밋 메시지는 이력이라 고치지 않았습니다 |
| `v0.3-pi` | 실제 파이에서 배포해 보고 드러난 빈틈을 고친 판(12장). `docs/pi-deploy-run.txt` 가 그 배포 기록 |

## 라이선스

MIT. `LICENSE` 를 보십시오.
