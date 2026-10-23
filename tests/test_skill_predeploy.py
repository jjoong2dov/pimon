""".claude/skills/predeploy-check 의 check_service.py 시험.

unit 파일의 ExecStart·ExecStopPost 를 지금 pimon 명령행에 실제로 넣어 보아(실행은 하지 않음)
옵션이 맞는지, ExecStopPost 의 --state-path 가 설정의 led.state_path 와 같은지 본다.
"""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CLAUDE_DIR = Path(os.environ.get("PIMON_CLAUDE_DIR") or ROOT / ".claude")
SKILL = CLAUDE_DIR / "skills" / "predeploy-check"
CHECK = SKILL / "check_service.py"

GOOD_START = "ExecStart=/usr/bin/python3 -m pimon --config /etc/pimon/pimon.toml"
GOOD_STOP = "ExecStopPost=/usr/bin/python3 -m pimon --restore-led --state-path /run/pimon/led.json"


def unit(tmp_path, *lines):
    p = tmp_path / "pimon.service"
    p.write_text("[Service]\n" + "\n".join(lines) + "\n", encoding="utf-8")
    return p


def toml(tmp_path, text=""):
    p = tmp_path / "pimon.toml"
    p.write_text(text, encoding="utf-8")
    return p


def run(service, cfg):
    return subprocess.run([sys.executable, str(CHECK), str(service), str(cfg)],
                          capture_output=True, text=True, timeout=30)


def test_skill_file_exists():
    text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    assert text.startswith("---\n")
    assert "description:" in text
    assert "disable-model-invocation: true" in text


def test_repo_deploy_files_pass():
    r = run(ROOT / "deploy" / "pimon.service", ROOT / "deploy" / "pimon.toml")
    assert r.returncode == 0, r.stdout + r.stderr


def test_good(tmp_path):
    r = run(unit(tmp_path, GOOD_START, GOOD_STOP), toml(tmp_path))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "ExecStart" in r.stdout and "ExecStopPost" in r.stdout


def test_unknown_option_fails(tmp_path):
    r = run(unit(tmp_path, GOOD_START + " --verbose", GOOD_STOP), toml(tmp_path))
    assert r.returncode == 1
    assert "ExecStart" in r.stdout and "--verbose" in r.stdout


def test_state_path_without_restore_fails(tmp_path):
    stop = "ExecStopPost=/usr/bin/python3 -m pimon --state-path /run/pimon/led.json"
    r = run(unit(tmp_path, GOOD_START, stop), toml(tmp_path))
    assert r.returncode == 1
    assert "ExecStopPost" in r.stdout


def test_start_must_serve(tmp_path):
    r = run(unit(tmp_path, GOOD_START + " --once", GOOD_STOP), toml(tmp_path))
    assert r.returncode == 1


def test_stop_must_restore(tmp_path):
    r = run(unit(tmp_path, GOOD_START, GOOD_START.replace("ExecStart", "ExecStopPost")),
            toml(tmp_path))
    assert r.returncode == 1


def test_state_path_mismatch_fails(tmp_path):
    cfg = toml(tmp_path, '[led]\nstate_path = "/run/other/led.json"\n')
    r = run(unit(tmp_path, GOOD_START, GOOD_STOP), cfg)
    assert r.returncode == 1
    assert "/run/other/led.json" in r.stdout


def test_missing_line_fails(tmp_path):
    r = run(unit(tmp_path, GOOD_START), toml(tmp_path))
    assert r.returncode == 1
    assert "ExecStopPost" in r.stdout


def test_not_pimon_fails(tmp_path):
    r = run(unit(tmp_path, "ExecStart=/usr/bin/python3 -m other", GOOD_STOP), toml(tmp_path))
    assert r.returncode == 1
