"""설정: 기본값 + TOML 파일 읽기 + 값 검사.

파일이 없으면 기본값으로 돈다. 모르는 키, 형식이 틀린 값, warn ≥ crit 같은
모순은 ConfigError 로 멈춘다.
"""

import copy
import math
import os
import tomllib

DEFAULT_PATH = "/etc/pimon/pimon.toml"

DEFAULTS = {
    "interval_s": 5,
    "http": {"bind": "127.0.0.1", "port": 8080},
    # state_path: 시작 때 LED 원래 상태를 적는 파일. --root 아래로 붙이지 않는 실제 경로.
    "led": {"enabled": True, "state_path": "/run/pimon/led.json"},
    "temp": {"warn_c": 70, "crit_c": 80},
    "memory": {"warn_used_pct": 80, "crit_used_pct": 90},
    "disk": {"paths": ["/"], "warn_used_pct": 80, "crit_used_pct": 90},
    "link": {"iface": "eth0"},
}


# interval_s 범위. 위: Event.wait 는 1e10 초쯤부터 OverflowError. 아래: 쉬지 않고 도는 것을 막는다
# (시험은 0.05 로 돌린다).
INTERVAL_MIN_S = 0.01
INTERVAL_MAX_S = 86400


class ConfigError(ValueError):
    pass


def load(path=DEFAULT_PATH):
    """설정 파일을 읽어 기본값에 덮어쓴 dict 를 돌려준다."""
    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
    except FileNotFoundError:
        data = {}
    except UnicodeDecodeError as e:
        raise ConfigError(f"{path}: UTF-8 이 아님: {e}") from None
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{path}: TOML 형식 오류: {e}") from None
    except OSError as e:
        raise ConfigError(f"{path}: 읽을 수 없음: {e}") from None
    cfg = _merge(data)
    _check(cfg)
    return cfg


def _merge(data):
    cfg = copy.deepcopy(DEFAULTS)
    for key, value in data.items():
        if key not in DEFAULTS:
            raise ConfigError(f"모르는 키: {key}")
        if isinstance(DEFAULTS[key], dict):
            if not isinstance(value, dict):
                raise ConfigError(f"{key}: [{key}] 표여야 함")
            for sub, v in value.items():
                if sub not in DEFAULTS[key]:
                    raise ConfigError(f"모르는 키: {key}.{sub}")
                cfg[key][sub] = v
        else:
            cfg[key] = value
    return cfg


def _is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _num(name, v, lo=None, hi=None, lo_open=False):
    if not _is_num(v):
        raise ConfigError(f"{name}: 숫자여야 함 ({v!r})")
    if not math.isfinite(v):             # nan 은 모든 비교가 거짓이라 아래 검사를 지나간다
        raise ConfigError(f"{name}: nan·inf 는 안 됨 ({v!r})")
    if lo is not None and (v <= lo if lo_open else v < lo):
        raise ConfigError(f"{name}: {'>' if lo_open else '≥'} {lo} 이어야 함 ({v!r})")
    if hi is not None and v > hi:
        raise ConfigError(f"{name}: ≤ {hi} 이어야 함 ({v!r})")


def _warn_crit(section, cfg, unit, lo=None, hi=None):
    warn_key, crit_key = f"warn_{unit}", f"crit_{unit}"
    sec = cfg[section]
    _num(f"{section}.{warn_key}", sec[warn_key], lo, hi)
    _num(f"{section}.{crit_key}", sec[crit_key], lo, hi)
    if sec[warn_key] >= sec[crit_key]:
        raise ConfigError(f"{section}: {warn_key}({sec[warn_key]}) 는 "
                          f"{crit_key}({sec[crit_key]}) 보다 작아야 함")


def _check(cfg):
    _num("interval_s", cfg["interval_s"], lo=INTERVAL_MIN_S, hi=INTERVAL_MAX_S)

    bind = cfg["http"]["bind"]
    if not isinstance(bind, str) or not bind:
        raise ConfigError(f"http.bind: 빈 문자열이 아닌 문자열이어야 함 ({bind!r})")
    port = cfg["http"]["port"]
    if not isinstance(port, int) or isinstance(port, bool) or not 0 <= port <= 65535:
        raise ConfigError(f"http.port: 0~65535 정수여야 함 ({port!r})")

    if not isinstance(cfg["led"]["enabled"], bool):
        raise ConfigError(f"led.enabled: true/false 여야 함 ({cfg['led']['enabled']!r})")
    state = cfg["led"]["state_path"]
    if (not isinstance(state, str) or not os.path.isabs(state)
            or not os.path.basename(state)):
        raise ConfigError(f"led.state_path: '/' 로 시작하는 파일 경로여야 함 ({state!r})")

    _warn_crit("temp", cfg, "c")
    _warn_crit("memory", cfg, "used_pct", 0, 100)
    _warn_crit("disk", cfg, "used_pct", 0, 100)

    paths = cfg["disk"]["paths"]
    if (not isinstance(paths, list) or not paths
            or not all(isinstance(p, str) and p.startswith("/") for p in paths)):
        raise ConfigError(f"disk.paths: '/' 로 시작하는 경로 목록이어야 함 ({paths!r})")

    iface = cfg["link"]["iface"]
    if not isinstance(iface, str) or not iface or "/" in iface or iface in (".", ".."):
        raise ConfigError(f"link.iface: 인터페이스 이름이어야 함 ({iface!r})")
