"""설정 파일(TOML) 읽기와 검사."""

import copy
import ipaddress
import math
import tomllib
from pathlib import PurePosixPath, Path

DEFAULTS = {
    "root": "/",
    "state_dir": "/run/pimon",
    "interval": 5.0,
    "listen": "127.0.0.1:8080",
    "iface": "eth0",
    "disk_path": "/",
    "throttle_source": "auto",
    "led": {"enabled": True, "name": "auto"},
    "limits": {
        "temp_warn": 70.0,
        "temp_crit": 80.0,
        "disk_warn": 80.0,
        "disk_crit": 90.0,
        "mem_warn": 85.0,
        "mem_crit": 95.0,
    },
}

THROTTLE_SOURCES = ("auto", "sysfs", "vcgencmd", "none")

# 한계값이 들어갈 수 있는 범위
RANGES = {"temp": (0.0, 125.0), "disk": (0.0, 100.0), "mem": (0.0, 100.0)}


class ConfigError(ValueError):
    pass


def _merge(base, extra, where=""):
    for key, value in extra.items():
        name = f"{where}{key}"
        if key not in base:
            raise ConfigError(f"모르는 설정: {name}")
        if isinstance(base[key], dict):
            if not isinstance(value, dict):
                raise ConfigError(f"{name} 는 [표] 여야 합니다")
            _merge(base[key], value, f"{name}.")
        else:
            base[key] = value


def _number(node, key, name):
    value = node[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"{name} 는 숫자여야 합니다: {value!r}")
    if not math.isfinite(value):
        raise ConfigError(f"{name} 에 nan·inf 는 쓸 수 없습니다")
    node[key] = float(value)


def _text(node, key, name):
    value = node[key]
    if not isinstance(value, str) or not value or "\0" in value:
        raise ConfigError(f"{name} 는 비어 있지 않은 문자열이어야 합니다: {value!r}")


def validate(cfg):
    for key in ("root", "state_dir", "listen", "iface", "disk_path", "throttle_source"):
        _text(cfg, key, key)
    _text(cfg["led"], "name", "led.name")

    _number(cfg, "interval", "interval")
    if not 1 <= cfg["interval"] <= 3600:
        raise ConfigError("interval 은 1~3600 초여야 합니다")
    for key in DEFAULTS["limits"]:
        _number(cfg["limits"], key, f"limits.{key}")
    limits = cfg["limits"]
    for name, (low, high) in RANGES.items():
        warn, crit = limits[f"{name}_warn"], limits[f"{name}_crit"]
        if not low <= warn < crit <= high:
            raise ConfigError(f"limits 는 {low:g} <= {name}_warn < {name}_crit <= {high:g} 이어야 합니다")

    if cfg["throttle_source"] not in THROTTLE_SOURCES:
        raise ConfigError(f"throttle_source 는 {', '.join(THROTTLE_SOURCES)} 중 하나: {cfg['throttle_source']!r}")
    if "/" in cfg["iface"] or cfg["iface"] in (".", ".."):
        raise ConfigError(f"iface 이름이 이상합니다: {cfg['iface']!r}")
    name = cfg["led"]["name"]
    if "/" in name or name in (".", ".."):
        raise ConfigError(f"led.name 이 이상합니다: {name!r}")
    if ".." in PurePosixPath(cfg["disk_path"]).parts:
        raise ConfigError("disk_path 에 .. 는 쓸 수 없습니다")
    if not isinstance(cfg["led"]["enabled"], bool):
        raise ConfigError("led.enabled 는 true 또는 false")
    listen_addr(cfg)
    return cfg


def load(path=None):
    cfg = copy.deepcopy(DEFAULTS)
    if path is not None:
        try:
            data = tomllib.loads(Path(path).read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as e:
            raise ConfigError(f"{path}: TOML 문법 오류: {e}") from None
        except UnicodeDecodeError:
            raise ConfigError(f"{path}: UTF-8 로 저장해야 합니다") from None
        _merge(cfg, data)
    return validate(cfg)


def listen_addr(cfg):
    """'IPv4주소:포트' 를 (주소, 포트) 로. IPv6 는 지원하지 않는다."""
    host, sep, port = cfg["listen"].rpartition(":")
    try:
        ipaddress.IPv4Address(host)
    except ValueError:
        raise ConfigError(f"listen 은 'IPv4주소:포트' 형식이어야 합니다: {cfg['listen']!r}") from None
    if not sep or not port.isdigit() or not 0 < int(port) < 65536:
        raise ConfigError(f"listen 은 'IPv4주소:포트' 형식이어야 합니다: {cfg['listen']!r}")
    return host, int(port)
