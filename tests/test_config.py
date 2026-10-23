import pytest

from pimon import config


def write(tmp_path, text):
    p = tmp_path / "pimon.toml"
    p.write_text(text, encoding="utf-8")
    return p


def test_defaults_without_file():
    cfg = config.load()
    assert cfg["interval"] == 5.0
    assert cfg["limits"]["temp_crit"] == 80.0


def test_file_overrides_only_given_keys(tmp_path):
    cfg = config.load(write(tmp_path, 'interval = 2\n[limits]\ntemp_warn = 60\n'))
    assert cfg["interval"] == 2.0
    assert cfg["limits"]["temp_warn"] == 60.0
    assert cfg["limits"]["temp_crit"] == 80.0


@pytest.mark.parametrize("text, words", [
    ("intervall = 2\n", "모르는 설정: intervall"),
    ("[limits]\ntemp_warn = 85\n", "temp_warn < temp_crit"),
    ('[limits]\ntemp_warn = "70"\n', "숫자여야"),
    ("interval = true\n", "숫자여야"),
    ("interval = 0.5\n", "1~3600"),
    ('listen = "8080"\n', "주소:포트"),
    ('throttle_source = "gpio"\n', "throttle_source"),
    ("interval = \n", "TOML 문법 오류"),
    ('limits = 3\n', "[표]"),
    ("iface = 3\n", "iface 는 비어 있지 않은 문자열"),
    ('root = ""\n', "root 는 비어 있지 않은 문자열"),
    ("disk_path = [1]\n", "disk_path 는"),
    ("[led]\nname = 1\n", "led.name 는"),
    ("interval = nan\n", "nan"),
    ("[limits]\ntemp_crit = inf\n", "nan"),
    ("[limits]\ndisk_crit = 120\n", "disk_crit <= 100"),
    ('listen = "::1:8080"\n', "IPv4"),
    ('listen = "[::1]:8080"\n', "IPv4"),
    ('listen = "localhost:8080"\n', "IPv4"),
    ('disk_path = "/../etc"\n', r"\.\."),
    ('iface = "../x"\n', "iface 이름"),
])
def test_bad_config(tmp_path, text, words):
    with pytest.raises(config.ConfigError, match=words if words.startswith("\\") else words.replace("[", r"\[").replace("]", r"\]")):
        config.load(write(tmp_path, text))


def test_listen_addr():
    assert config.listen_addr({"listen": "0.0.0.0:9100"}) == ("0.0.0.0", 9100)


def test_not_utf8(tmp_path):
    p = tmp_path / "pimon.toml"
    p.write_bytes('iface = "랜"\n'.encode("euc-kr"))
    with pytest.raises(config.ConfigError, match="UTF-8"):
        config.load(p)
