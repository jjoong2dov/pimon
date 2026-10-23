"""설정 파일(TOML) 읽기와 검사."""

import copy
import tomllib
from pathlib import Path

DEFAULTS = {
    "root": "/",
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


def _number(cfg, *keys):
    node = cfg
    for key in keys[:-1]:
        node = node[key]
    value = node[keys[-1]]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"{'.'.join(keys)} 는 숫자여야 합니다: {value!r}")
    node[keys[-1]] = float(value)


def validate(cfg):
    _number(cfg, "interval")
    if cfg["interval"] < 1:
        raise ConfigError("interval 은 1초 이상이어야 합니다")
    for key in DEFAULTS["limits"]:
        _number(cfg, "limits", key)
    limits = cfg["limits"]
    for name in ("temp", "disk", "mem"):
        if limits[f"{name}_warn"] >= limits[f"{name}_crit"]:
            raise ConfigError(f"limits.{name}_warn 은 {name}_crit 보다 작아야 합니다")
    if cfg["throttle_source"] not in THROTTLE_SOURCES:
        raise ConfigError(f"throttle_source 는 {', '.join(THROTTLE_SOURCES)} 중 하나: {cfg['throttle_source']!r}")
    host, sep, port = str(cfg["listen"]).rpartition(":")
    if not sep or not host or not port.isdigit() or not 0 < int(port) < 65536:
        raise ConfigError(f"listen 은 '주소:포트' 형식이어야 합니다: {cfg['listen']!r}")
    if not isinstance(cfg["led"]["enabled"], bool):
        raise ConfigError("led.enabled 는 true 또는 false")
    return cfg


def load(path=None):
    cfg = copy.deepcopy(DEFAULTS)
    if path is not None:
        try:
            data = tomllib.loads(Path(path).read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as e:
            raise ConfigError(f"{path}: TOML 문법 오류: {e}") from None
        _merge(cfg, data)
    return validate(cfg)


def listen_addr(cfg):
    host, _, port = cfg["listen"].rpartition(":")
    return host, int(port)
