import shlex
from pathlib import Path

import pytest

from pimon import __main__ as main

UNIT = Path(__file__).resolve().parent.parent / "deploy" / "pimon.service"


def pimon_commands():
    """unit 의 Exec* 줄 가운데 'python3 -m pimon ...' 의 pimon 뒤 인자들."""
    found = []
    for line in UNIT.read_text().splitlines():
        key, _, value = line.partition("=")
        if not key.startswith("Exec"):
            continue
        words = shlex.split(value)
        if words[1:3] == ["-m", "pimon"]:
            found.append((key, words[3:]))
    return found


def test_unit_runs_right_commands():
    # argparse 가 받아들이는 것만으로는 부족하다. 예: ExecStart 가 restore-led 여도 통과한다
    commands = {key: args[0] for key, args in pimon_commands()}
    assert commands == {"ExecStart": "run", "ExecStopPost": "restore-led"}


@pytest.mark.parametrize("key,args", pimon_commands())
def test_unit_command_is_valid(key, args):
    # 시험은 통과하는데 파이에서 다시 시작하자마자 죽는 판을 여기서 막는다
    try:
        main.parser().parse_args(args)
    except SystemExit:
        pytest.fail(f"{key} 의 pimon 인자가 틀림: {args}")
