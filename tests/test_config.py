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
        "led": {"enabled": True},
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
