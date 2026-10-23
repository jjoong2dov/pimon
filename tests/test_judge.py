import pytest

from pimon import config, judge

LIMITS = config.DEFAULTS["limits"]


@pytest.mark.parametrize("value, level", [(69.9, "ok"), (70.0, "warn"), (79.9, "warn"), (80.0, "crit")])
def test_temp_edges(value, level):
    assert judge.judge_one("temp", {"value": value}, LIMITS)[0] == level


def test_throttled_now_is_crit_past_is_warn():
    assert judge.judge_one("throttled", {"flags": ["throttled", "throttled_occurred"]}, LIMITS) == ("crit", "throttled")
    assert judge.judge_one("throttled", {"flags": ["under_voltage_occurred"]}, LIMITS)[0] == "warn"
    assert judge.judge_one("throttled", {"flags": []}, LIMITS)[0] == "ok"


def test_error_is_unknown():
    assert judge.judge_one("temp", {"error": "FileNotFoundError: x"}, LIMITS) == ("unknown", "FileNotFoundError: x")


def test_link_down_is_crit():
    assert judge.judge_one("link", {"value": "down"}, LIMITS)[0] == "crit"


def test_overall_is_worst():
    checks = {"temp": {"value": 75.0}, "link": {"value": "up"}, "mem": {"error": "x"}}
    assert judge.judge(checks, LIMITS) == "warn"
    assert checks["mem"]["level"] == "unknown"
    assert judge.worst(["ok", "unknown"]) == "unknown"
