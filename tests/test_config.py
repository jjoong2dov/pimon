import pytest

from pimon.config import DEFAULTS, ConfigError, load


def write(tmp_path, text):
    p = tmp_path / "pimon.toml"
    p.write_text(text)
    return p


def test_missing_file_gives_defaults(tmp_path):
    cfg = load(tmp_path / "없음.toml")
    assert cfg == DEFAULTS


def test_defaults_match_plan():
    assert DEFAULTS == {
        "interval_s": 5,
        "http": {"bind": "127.0.0.1", "port": 8080},
        "led": {"enabled": True, "state_path": "/run/pimon/led.json"},
        "temp": {"warn_c": 70, "crit_c": 80},
        "memory": {"warn_used_pct": 80, "crit_used_pct": 90},
        "disk": {"paths": ["/"], "warn_used_pct": 80, "crit_used_pct": 90},
        "link": {"iface": "eth0"},
    }


def test_load_does_not_share_defaults(tmp_path):
    cfg = load(tmp_path / "없음.toml")
    cfg["disk"]["paths"].append("/boot")
    assert DEFAULTS["disk"]["paths"] == ["/"]


def test_partial_file_merges_with_defaults(tmp_path):
    p = write(tmp_path, '[temp]\nwarn_c = 65\n\n[http]\nbind = "0.0.0.0"\n')
    cfg = load(p)
    assert cfg["temp"] == {"warn_c": 65, "crit_c": 80}
    assert cfg["http"] == {"bind": "0.0.0.0", "port": 8080}
    assert cfg["memory"] == DEFAULTS["memory"]


def test_full_file(tmp_path):
    p = write(tmp_path, """
interval_s = 2.5
[http]
bind = "0.0.0.0"
port = 9000
[led]
enabled = false
[temp]
warn_c = 60.5
crit_c = 75
[memory]
warn_used_pct = 70
crit_used_pct = 95
[disk]
paths = ["/", "/boot/firmware"]
warn_used_pct = 85
crit_used_pct = 95
[link]
iface = "wlan0"
""")
    cfg = load(p)
    assert cfg["interval_s"] == 2.5
    assert cfg["http"]["port"] == 9000
    assert cfg["led"]["enabled"] is False
    assert cfg["disk"]["paths"] == ["/", "/boot/firmware"]
    assert cfg["link"]["iface"] == "wlan0"


@pytest.mark.parametrize("text, words", [
    ("이건 toml 아님 = = =", "TOML"),
    ("intervals = 5\n", "intervals"),                        # 모르는 최상위 키
    ("[temp]\nwarn = 70\n", "temp.warn"),                    # 모르는 하위 키
    ("[gpu]\nx = 1\n", "gpu"),                               # 모르는 표
    ("http = 1\n", "http"),                                  # 표 자리에 값
    ("interval_s = 0\n", "interval_s"),
    ("interval_s = -1\n", "interval_s"),
    ('interval_s = "5"\n', "interval_s"),
    ("interval_s = true\n", "interval_s"),                   # bool 은 숫자로 치지 않음
    ("[http]\nport = 70000\n", "http.port"),
    ("[http]\nport = 80.5\n", "http.port"),
    ("[http]\nport = true\n", "http.port"),
    ('[http]\nbind = ""\n', "http.bind"),
    ("[led]\nenabled = 1\n", "led.enabled"),
    ('[led]\nstate_path = ""\n', "led.state_path"),
    ('[led]\nstate_path = "run/led.json"\n', "led.state_path"),     # 절대 경로만
    ('[led]\nstate_path = "/run/pimon/"\n', "led.state_path"),      # 파일 이름 없음
    ("[led]\nstate_path = 1\n", "led.state_path"),
    ("[temp]\nwarn_c = 80\ncrit_c = 80\n", "temp"),          # warn ≥ crit
    ("[temp]\nwarn_c = 85\n", "temp"),                       # 기본 crit 80 보다 큼
    ('[temp]\ncrit_c = "90"\n', "temp.crit_c"),
    ("[memory]\nwarn_used_pct = 95\n", "memory"),
    ("[memory]\ncrit_used_pct = 101\n", "memory.crit_used_pct"),
    ("[memory]\nwarn_used_pct = -1\n", "memory.warn_used_pct"),
    ("[disk]\npaths = []\n", "disk.paths"),
    ('[disk]\npaths = "/"\n', "disk.paths"),
    ('[disk]\npaths = ["data"]\n', "disk.paths"),            # 절대 경로만
    ("[disk]\npaths = [1]\n", "disk.paths"),
    ("[disk]\nwarn_used_pct = 90\ncrit_used_pct = 90\n", "disk"),
    ('[link]\niface = ""\n', "link.iface"),
    ('[link]\niface = "../eth0"\n', "link.iface"),           # 경로 벗어나기 막기
])
def test_bad_config_raises(tmp_path, text, words):
    p = write(tmp_path, text)
    with pytest.raises(ConfigError) as e:
        load(p)
    assert words in str(e.value)


# 검토 3: nan 은 모든 비교가 거짓이라 warn ≥ crit 검사도 지나가고 판정이 늘 ok 가 된다.
@pytest.mark.parametrize("text, words", [
    ("interval_s = inf\n", "interval_s"),
    ("interval_s = nan\n", "interval_s"),
    ("[temp]\nwarn_c = nan\n", "temp.warn_c"),
    ("[temp]\ncrit_c = inf\n", "temp.crit_c"),
    ("[temp]\nwarn_c = -inf\n", "temp.warn_c"),
    ("[memory]\nwarn_used_pct = nan\n", "memory.warn_used_pct"),
    ("[disk]\ncrit_used_pct = nan\n", "disk.crit_used_pct"),
])
def test_non_finite_numbers_rejected(tmp_path, text, words):
    p = write(tmp_path, text)
    with pytest.raises(ConfigError) as e:
        load(p)
    assert words in str(e.value)


# 검토 8: UTF-8 이 아닌 파일은 날 UnicodeDecodeError 가 아니라 ConfigError 로 멈춘다.
def test_non_utf8_file_is_config_error(tmp_path):
    p = tmp_path / "pimon.toml"
    p.write_bytes("# 주석\n[temp]\nwarn_c = 65\n".encode("euc-kr"))
    with pytest.raises(ConfigError) as e:
        load(p)
    assert "UTF-8" in str(e.value)


# Codex 검토 8: 너무 큰 값은 Event.wait 에서 OverflowError (1e10 부터), 너무 작은 값은 쉬지 않고 돈다.
@pytest.mark.parametrize("value", ["1e308", "1e10", "86401", "0.009", "1e-300"])
def test_interval_out_of_range_rejected(tmp_path, value):
    with pytest.raises(ConfigError) as e:
        load(write(tmp_path, f"interval_s = {value}\n"))
    assert "interval_s" in str(e.value)


@pytest.mark.parametrize("value", ["0.01", "0.05", "5", "86400"])
def test_interval_range_edges_accepted(tmp_path, value):
    assert load(write(tmp_path, f"interval_s = {value}\n"))["interval_s"] == float(value)


def test_interval_max_is_below_event_wait_limit():
    import threading
    from pimon.config import INTERVAL_MAX_S
    assert INTERVAL_MAX_S < threading.TIMEOUT_MAX


def test_deploy_unit_restore_path_matches_default_state_path():
    """Codex 검토 3: ExecStopPost 는 설정을 읽지 않으므로 --state-path 가 기본 state_path 와 같아야 한다."""
    from pathlib import Path
    unit = (Path(__file__).resolve().parents[1] / "deploy" / "pimon.service").read_text()
    lines = [l for l in unit.splitlines() if l.startswith("ExecStopPost=")]
    assert len(lines) == 1
    assert f"--state-path {DEFAULTS['led']['state_path']}" in lines[0]
    assert "--config" not in lines[0]


def test_deploy_example_matches_defaults():
    from pathlib import Path
    assert load(Path(__file__).resolve().parents[1] / "deploy" / "pimon.toml") == DEFAULTS


def test_led_state_path_can_be_changed(tmp_path):
    p = write(tmp_path, '[led]\nstate_path = "/var/lib/pimon/led.json"\n')
    assert load(p)["led"] == {"enabled": True, "state_path": "/var/lib/pimon/led.json"}
