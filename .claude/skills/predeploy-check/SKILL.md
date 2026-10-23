---
name: predeploy-check
description: pimon 배포 전 점검. 작업 트리, 단위 시험, deploy/pimon.toml 설정 검사, deploy/pimon.service 의 명령행 옵션, 지난 deploy-* 태그 이후 바뀐 파일을 확인해 표 하나로 보고한다. 점검만 하고 배포는 하지 않는다.
disable-model-invocation: true
allowed-tools: Bash(git status *) Bash(git describe *) Bash(git diff *) Bash(.venv/bin/python -m pytest *) Bash(.venv/bin/python -c *) Bash(.venv/bin/python .claude/skills/predeploy-check/check_service.py *)
---

# 배포 전 점검

## 절대 하지 않는 것

이 스킬은 **점검만** 한다. 배포 자체는 절대 하지 않는다.

- `ssh`, `scp`, `rsync` 등으로 파이에 접속하거나 파일을 보내지 않는다.
- `deploy/` 안의 스크립트를 실행하지 않는다 (`deploy/*`, `bash deploy/...`, `sh deploy/...` 모두).
- `systemctl`, `sudo` 를 쓰지 않는다.
- 태그를 만들거나(`git tag`), 커밋·푸시하지 않는다. 점검 중 파일을 고치지도 않는다.
- 점검이 모두 통과해도 배포를 제안·실행하지 않는다. 배포는 사람이 표를 보고 직접 한다.

## 순서

저장소 루트에서 아래 다섯 항목을 차례로 돌린다. 앞 항목이 실패해도 나머지는 모두 돌린다.
각 항목에서 **근거** 로 쓸 출력 한 줄을 그대로 옮긴다 (요약하거나 지어내지 않는다).

1. **작업 트리** — `git status --porcelain`
   - 출력이 비면 통과. 근거: `git status --porcelain: (출력 없음)`
   - 한 줄이라도 있으면 실패. 근거: 첫 줄 (여러 줄이면 뒤에 `외 N개`).

2. **단위 시험** — `.venv/bin/python -m pytest -q`
   - 종료 코드 0 이면 통과. 근거: 마지막 요약 줄 (예: `357 passed in 20.9s`).
   - 아니면 실패. 근거: 마지막 요약 줄.

3. **설정 검사 (deploy/pimon.toml)** — pimon 이 시작할 때 쓰는 `config.load()` 그대로 검사한다:
   ```
   .venv/bin/python -c "import os,sys; from pimon import config; p='deploy/pimon.toml'; os.path.isfile(p) or sys.exit('파일 없음: '+p); c=config.load(p); print('OK', p, 'interval_s=%s http=%s:%s led.state_path=%s' % (c['interval_s'], c['http']['bind'], c['http']['port'], c['led']['state_path']))"
   ```
   (`config.load` 는 파일이 없으면 기본값으로 통과하므로 파일이 있는지 먼저 본다.)
   - `OK ...` 가 찍히고 종료 코드 0 이면 통과. 근거: 그 줄.
   - `ConfigError` 나 다른 오류면 실패. 근거: 오류 메시지 줄.

4. **unit 명령행 (deploy/pimon.service)** —
   `.venv/bin/python .claude/skills/predeploy-check/check_service.py deploy/pimon.service deploy/pimon.toml`
   - ExecStart·ExecStopPost 를 지금 `pimon/__main__.py` 의 명령행에 실제로 넣어 보되, 주기 실행·LED 복구는
     가로채서 아무것도 실행하지 않는다. ExecStart 는 주기 실행으로, ExecStopPost 는 LED 복구로 가야 하고,
     ExecStopPost 의 `--state-path` 는 pimon.toml 의 `led.state_path` 와 같아야 한다.
   - 종료 코드 0 이면 통과, 아니면 실패. 근거: `실패` 로 시작하는 첫 줄, 없으면 `ExecStopPost` 줄.
     표의 근거 칸에는 한 줄만 넣고, 다른 줄은 표 아래 메모에 적어도 된다.

5. **지난 배포 이후 바뀐 파일** —
   `git describe --tags --abbrev=0 --match 'deploy-*' HEAD` 로 가장 가까운 배포 태그를 찾고,
   `git diff --name-only <태그>..HEAD` 로 바뀐 파일 목록을 얻는다.
   - 태그를 찾고 목록을 얻으면 통과. 근거: `<태그> 이후 N개 파일: a, b, c ...` (많으면 앞 몇 개와 개수).
     전체 목록은 표 아래에 적는다.
   - `deploy-*` 태그가 없으면 실패. 근거: git 의 오류 줄 (예: `fatal: No names found ...`).
     지난 배포와 비교할 수 없다는 뜻이다. 태그는 만들지 않는다.

## 보고

결과는 아래 표 **하나** 로 낸다. 결과 칸은 `통과` 또는 `실패` 만 쓴다.

| 항목 | 결과 | 근거 |
|---|---|---|
| 작업 트리 | 통과 | `git status --porcelain: (출력 없음)` |
| 단위 시험 | ... | ... |
| 설정 검사 (deploy/pimon.toml) | ... | ... |
| unit 명령행 (deploy/pimon.service) | ... | ... |
| 지난 배포 이후 바뀐 파일 | ... | ... |

표 아래에는 필요할 때만 짧게: 5번의 전체 파일 목록, 실패 항목의 나머지 출력. 고치는 방법을 제안할 수는 있지만
고치지는 않는다. 마지막에 "배포는 하지 않았다" 를 한 줄로 적는다.
