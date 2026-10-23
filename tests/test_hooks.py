""".claude/ 설정과 훅 스크립트 시험.

훅은 Claude Code 가 하듯 stdin 에 JSON 을 넣고 하위 프로세스로 돌려 본다.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
# 보통은 저장소의 .claude/. 다른 곳의 사본을 시험하려면 PIMON_CLAUDE_DIR 로 바꾼다.
CLAUDE_DIR = Path(os.environ.get("PIMON_CLAUDE_DIR") or ROOT / ".claude")
HOOKS = CLAUDE_DIR / "hooks"
BLOCK_SYS = HOOKS / "block_sys_write.py"
RUN_TESTS = HOOKS / "run_tests_after_edit.py"
SETTINGS = CLAUDE_DIR / "settings.json"


def run_hook(script, payload, env_extra=None, timeout=60):
    env = dict(os.environ)
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, str(script)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=env,
        timeout=timeout,
    )


# ---------------------------------------------------------------------------
# settings.json

@pytest.fixture(scope="module")
def settings():
    return json.loads(SETTINGS.read_text(encoding="utf-8"))


def test_settings_allow(settings):
    allow = settings["permissions"]["allow"]
    for rule in [
        "Bash(.venv/bin/python -m pytest *)",
        "Bash(python3 fake/make_fake.py *)",
        "Bash(python3 -m pimon *)",
        "Bash(git status *)",
        "Bash(git diff *)",
        "Bash(git log *)",
    ]:
        assert rule in allow


def test_settings_deny(settings):
    deny = settings["permissions"]["deny"]
    for rule in [
        "Bash(ssh *)",
        "Bash(scp *)",
        "Bash(sudo *)",
        "Bash(systemctl *)",
        "Bash(deploy/*)",
        "Bash(./deploy/*)",
        "Bash(bash deploy/*)",
        "Bash(sh deploy/*)",
    ]:
        assert rule in deny


def test_settings_hooks_point_to_existing_scripts(settings):
    pre = settings["hooks"]["PreToolUse"]
    post = settings["hooks"]["PostToolUse"]
    assert [m["matcher"] for m in pre] == ["Bash"]
    assert [m["matcher"] for m in post] == ["Edit|Write"]
    for group, script in [(pre[0], BLOCK_SYS), (post[0], RUN_TESTS)]:
        (hook,) = group["hooks"]
        assert hook["type"] == "command"
        assert "${CLAUDE_PROJECT_DIR}/.claude/hooks/" + script.name in hook["command"]
        assert script.is_file()


# ---------------------------------------------------------------------------
# block_sys_write.py

def bash(cmd, cwd=str(ROOT)):
    return {
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": cmd},
        "cwd": cwd,
    }


MUST_BLOCK = [
    "echo none > /sys/class/leds/ACT/trigger",
    "echo 1 >/sys/class/leds/ACT/brightness",
    "echo 1>>/sys/class/leds/ACT/brightness",
    "echo 0 2> /proc/sys/kernel/printk",
    "printf 1 &> /sys/class/leds/ACT/brightness",
    "echo 1 >| /sys/class/leds/ACT/brightness",
    "echo none | tee /sys/class/leds/ACT/trigger",
    "echo none | tee -a /sys/class/leds/ACT/trigger > /dev/null",
    "echo none | sudo tee /sys/class/leds/ACT/trigger",
    "echo 'none' > '/sys/class/leds/ACT/trigger'",
    'echo "none" > "/sys/class/leds/ACT/trigger"',
    "echo 1 > //sys/class/leds/ACT/brightness",
    "echo 1 > /tmp/../sys/class/leds/ACT/brightness",
    "ls && echo 1 > /sys/class/leds/ACT/brightness",
    "ls; echo 1 > /sys/class/leds/ACT/brightness",
    "ls\necho 1 > /sys/class/leds/ACT/brightness",
    "cd /sys/class/leds/ACT && echo none > trigger",
    "cd / && echo none > sys/class/leds/ACT/trigger",
    "bash -c 'echo none > /sys/class/leds/ACT/trigger'",
    "sh -c \"echo 1 | tee /sys/class/leds/ACT/brightness\"",
    "cp fake/pi/sys/class/leds/ACT/trigger /sys/class/leds/ACT/trigger",
    "mv /tmp/x /proc/sys/vm/swappiness",
    "dd if=/dev/zero of=/sys/class/leds/ACT/brightness bs=1 count=1",
    "truncate -s 0 /sys/class/leds/ACT/trigger",
    "sysctl -w vm.swappiness=10",
    "env FOO=1 tee /sys/class/leds/ACT/trigger < /dev/null",
    "FOO=1 echo 1 > /sys/class/leds/ACT/brightness",
    "python3 -c \"open('/sys/class/leds/ACT/trigger', 'w').write('none')\"",
    "python3 -c \"from pathlib import Path; Path('/sys/class/leds/ACT/trigger').write_text('none')\"",
    "echo 'unterminated > /sys/class/leds/ACT/trigger",
]

MUST_ALLOW = [
    "cat /sys/class/thermal/thermal_zone0/temp",
    "cat /proc/meminfo",
    "grep MemTotal /proc/meminfo > /tmp/mem.txt",
    "cp /proc/meminfo fake/pi/proc/meminfo",
    "echo none > fake/pi/sys/class/leds/ACT/trigger",
    "echo 1 | tee fake/pi/sys/class/leds/ACT/brightness",
    f"echo none > {ROOT}/fake/pi/sys/class/leds/ACT/trigger",
    "cd fake/pi && echo none > sys/class/leds/ACT/trigger",
    "echo 1 > /tmp/sys/class/leds/ACT/brightness",
    "echo 1 > ./sys/x",
    "echo hi >&2",
    "echo hi 2>/dev/null",
    "echo '> /sys/class/leds/ACT/trigger'",
    "git log --oneline -5",
    ".venv/bin/python -m pytest -q",
    "python3 fake/make_fake.py fake/pi --temp 82",
    "python3 -m pimon --root fake/pi --once",
    "python3 -c \"print(open('/proc/meminfo').read())\"",
    "sysctl vm.swappiness",
    "ls /sys/class/leds",
]


@pytest.mark.parametrize("cmd", MUST_BLOCK)
def test_block_sys_write_blocks(cmd):
    r = run_hook(BLOCK_SYS, bash(cmd))
    assert r.returncode == 2, (cmd, r.stdout, r.stderr)
    assert "/sys" in r.stderr or "/proc" in r.stderr


@pytest.mark.parametrize("cmd", MUST_ALLOW)
def test_block_sys_write_allows(cmd):
    r = run_hook(BLOCK_SYS, bash(cmd))
    assert r.returncode == 0, (cmd, r.stdout, r.stderr)


def test_block_sys_write_relative_path_uses_hook_cwd():
    # cwd 가 / 이면 sys/... 는 진짜 /sys 다.
    r = run_hook(BLOCK_SYS, bash("echo 1 > sys/class/leds/ACT/brightness", cwd="/"))
    assert r.returncode == 2


def test_block_sys_write_ignores_other_tools():
    payload = {"tool_name": "Read", "tool_input": {"file_path": "/sys/x"}}
    assert run_hook(BLOCK_SYS, payload).returncode == 0


def test_block_sys_write_bad_input_is_nonblocking_error():
    r = subprocess.run(
        [sys.executable, str(BLOCK_SYS)], input="not json",
        capture_output=True, text=True, timeout=30,
    )
    assert r.returncode == 1


# ---------------------------------------------------------------------------
# run_tests_after_edit.py
#
# 진짜 저장소의 시험을 돌리면 이 시험이 자기 자신을 다시 부르게 되므로,
# tmp 에 작은 가짜 프로젝트(.venv/bin/python + tests/)를 만들어 그곳을 가리킨다.

@pytest.fixture
def proj(tmp_path):
    # 심볼릭 링크로 걸면 venv 를 잃어 pytest 를 못 찾으므로 감싸는 스크립트로 둔다.
    (tmp_path / ".venv" / "bin").mkdir(parents=True)
    py = tmp_path / ".venv" / "bin" / "python"
    py.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n')
    py.chmod(0o755)
    (tmp_path / "pimon").mkdir()
    (tmp_path / "pimon" / "thing.py").write_text("X = 1\n")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_thing.py").write_text(
        "def test_ok():\n    assert 1 == 1\n"
    )
    return tmp_path


def edit(path, tool="Edit"):
    return {
        "hook_event_name": "PostToolUse",
        "tool_name": tool,
        "tool_input": {"file_path": str(path)},
        "tool_response": {"filePath": str(path)},
    }


def run_post(proj, path, tool="Edit"):
    return run_hook(RUN_TESTS, edit(path, tool),
                    env_extra={"CLAUDE_PROJECT_DIR": str(proj)}, timeout=120)


def add_failing_test(proj):
    (proj / "tests" / "test_bad.py").write_text(
        "def test_bad():\n    assert 1 == 2, 'BAD_MARKER'\n"
    )


@pytest.mark.parametrize("rel", ["pimon/thing.py", "tests/test_thing.py"])
@pytest.mark.parametrize("tool", ["Edit", "Write"])
def test_run_tests_passing_is_silent(proj, rel, tool):
    r = run_post(proj, proj / rel, tool)
    assert r.returncode == 0, r.stderr
    assert r.stderr == ""


@pytest.mark.parametrize("rel", ["pimon/thing.py", "tests/test_bad.py"])
def test_run_tests_failure_reported_to_claude(proj, rel):
    add_failing_test(proj)
    r = run_post(proj, proj / rel)
    assert r.returncode == 2
    assert "BAD_MARKER" in r.stderr
    assert "failed" in r.stderr


@pytest.mark.parametrize("rel", [
    "README.md",
    "pimon/page.html",
    "fake/make_fake.py",
    "deploy/pimon.toml",
    "docs/pimon.py",
    "pimonx/a.py",
])
def test_run_tests_skips_other_files(proj, rel):
    add_failing_test(proj)  # 돌렸다면 실패했을 것
    r = run_post(proj, proj / rel)
    assert r.returncode == 0
    assert r.stderr == ""


def test_run_tests_skips_files_outside_project(proj, tmp_path_factory):
    add_failing_test(proj)
    other = tmp_path_factory.mktemp("other") / "pimon" / "a.py"
    r = run_post(proj, other)
    assert r.returncode == 0


def test_run_tests_relative_path(proj):
    add_failing_test(proj)
    r = run_hook(RUN_TESTS, edit("pimon/thing.py"),
                 env_extra={"CLAUDE_PROJECT_DIR": str(proj)}, timeout=120)
    assert r.returncode == 2


def test_run_tests_missing_venv_is_nonblocking(proj):
    (proj / ".venv" / "bin" / "python").unlink()
    r = run_post(proj, proj / "pimon" / "thing.py")
    assert r.returncode == 1
    assert ".venv" in r.stderr
