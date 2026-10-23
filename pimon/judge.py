"""판정: 수집값 + 설정 → level / reason. 파일을 건드리지 않는 순수 함수.

level 은 ok < unknown < warn < crit 순서이고, 전체 판정은 가장 나쁜 것이다.
수집 결과에 error 가 있으면 항목 판정은 unknown 이다.
"""

LEVELS = ["ok", "unknown", "warn", "crit"]

THROTTLE_NAMES = {
    "under_voltage": "저전압",
    "freq_capped": "클럭 제한",
    "throttled": "스로틀",
    "soft_temp_limit": "온도 소프트 제한",
}


def worst(levels):
    levels = list(levels)
    if not levels:
        return "unknown"
    return max(levels, key=LEVELS.index)


def _by_threshold(v, warn, crit):
    if v >= crit:
        return "crit"
    if v >= warn:
        return "warn"
    return "ok"


def _temp(v, cfg):
    t = cfg["temp"]
    return _by_threshold(v["c"], t["warn_c"], t["crit_c"]), f"{v['c']:.1f}°C"


def _throttle(v, cfg):
    names = lambda bits: "·".join(THROTTLE_NAMES.get(b, b) for b in bits)
    if v["now"]:
        return "crit", f"지금 {names(v['now'])}"
    if v["past"]:
        return "warn", f"부팅 뒤 {names(v['past'])} 있었음"
    return "ok", ""


def _memory(v, cfg):
    m = cfg["memory"]
    return _by_threshold(v["used_pct"], m["warn_used_pct"], m["crit_used_pct"]), f"{v['used_pct']:.1f}%"


def _disk(v, cfg):
    """경로별로 판정해 가장 나쁜 것. 못 읽은 경로는 unknown 이라 다른 경로의 warn·crit 를 가리지 않는다."""
    d = cfg["disk"]

    def one(p):
        if "error" in p:
            return "unknown", f"{p['path']} 오류: {p['error']}"
        return (_by_threshold(p["used_pct"], d["warn_used_pct"], d["crit_used_pct"]),
                f"{p['path']} {p['used_pct']:.1f}%")

    judged = [one(p) for p in v]
    return worst(l for l, _ in judged), ", ".join(r for _, r in judged)


def _link(v, cfg):
    return ("ok", "up") if v["up"] else ("crit", "down")


def _system(v, cfg):
    return "ok", ""


RULES = {
    "temp": _temp,
    "throttle": _throttle,
    "memory": _memory,
    "disk": _disk,
    "link": _link,
    "system": _system,
}


def judge_item(name, item, cfg):
    """수집 결과 하나를 (level, reason) 으로 판정한다."""
    if item["error"] is not None:
        return "unknown", item["error"]
    return RULES[name](item["value"], cfg)


def judge_all(items, cfg):
    """collect_all 결과에 level·reason 을 붙이고 overall 을 계산한다. 입력은 바꾸지 않는다."""
    out = {}
    for name, item in items.items():
        level, reason = judge_item(name, item, cfg)
        out[name] = {**item, "level": level, "reason": reason}
    return {"overall": worst(i["level"] for i in out.values()), "items": out}
