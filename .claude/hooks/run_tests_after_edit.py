#!/usr/bin/env python3
"""PostToolUse 훅: pimon/ 또는 tests/ 의 .py 를 Edit/Write 한 뒤 단위 시험을 돌린다.

통과하면 조용히 exit 0.
실패하면 pytest 출력 끝부분을 stderr 에 쓰고 exit 2 (Claude 에게 전달된다).
.venv 가 없거나 입력이 이상하면 exit 1 (막지 않는 오류, 화면에 알림만).
"""

import json
import os
import subprocess
import sys
from pathlib import Path

WATCHED = ("pimon", "tests")
PYTEST_TIMEOUT = 170  # settings.json 의 훅 timeout(180) 보다 짧게
MAX_LINES = 80
MAX_CHARS = 6000


def target_in_project(file_path, project):
    p = Path(file_path)
    if not p.is_absolute():
        p = project / p
    try:
        rel = Path(os.path.normpath(p)).relative_to(project)
    except ValueError:
        return False
    return len(rel.parts) >= 2 and rel.parts[0] in WATCHED and rel.suffix == ".py"


def tail(text):
    lines = text.rstrip().splitlines()[-MAX_LINES:]
    return "\n".join(lines)[-MAX_CHARS:]


def main():
    try:
        data = json.load(sys.stdin)
    except (ValueError, OSError) as e:
        print(f"run_tests_after_edit: 입력 JSON 을 읽지 못함: {e}", file=sys.stderr)
        return 1
    file_path = (data.get("tool_input") or {}).get("file_path") or ""
    project = Path(os.environ.get("CLAUDE_PROJECT_DIR") or data.get("cwd") or os.getcwd())
    project = Path(os.path.normpath(project.resolve()))
    if not file_path or not target_in_project(file_path, project):
        return 0

    python = project / ".venv" / "bin" / "python"
    if not python.exists():
        print(f"run_tests_after_edit: {python} 가 없어 시험을 건너뜀 (.venv 를 만들 것)",
              file=sys.stderr)
        return 1

    cmd = [str(python), "-m", "pytest", "-q", "--tb=short", "-p", "no:cacheprovider"]
    try:
        r = subprocess.run(cmd, cwd=project, capture_output=True, text=True,
                           timeout=PYTEST_TIMEOUT)
    except subprocess.TimeoutExpired:
        print(f"{file_path} 수정 뒤 단위 시험이 {PYTEST_TIMEOUT}초 안에 끝나지 않음.",
              file=sys.stderr)
        return 2
    if r.returncode == 0:
        return 0
    print(
        f"{file_path} 수정 뒤 단위 시험 실패 (.venv/bin/python -m pytest -q, "
        f"종료 코드 {r.returncode}). 출력 끝부분:\n" + tail(r.stdout + r.stderr),
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
