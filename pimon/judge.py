"""수집값을 정상(ok)·주의(warn)·경고(crit)·모름(unknown)으로 판정."""

LEVELS = ("ok", "unknown", "warn", "crit")

NOW_FLAGS = {"under_voltage", "freq_capped", "throttled", "soft_temp_limit"}


def worst(levels):
    return max(levels, key=LEVELS.index, default="ok")


def _by_limit(value, warn, crit):
    if value >= crit:
        return "crit", f"{value} >= {crit}"
    if value >= warn:
        return "warn", f"{value} >= {warn}"
    return "ok", ""


def judge_one(name, item, limits):
    if "error" in item:
        return "unknown", item["error"]
    if item.get("skipped"):
        return "ok", "검사 안 함"
    if name in ("temp", "disk", "mem"):
        return _by_limit(item["value"], limits[f"{name}_warn"], limits[f"{name}_crit"])
    if name == "throttled":
        now = sorted(NOW_FLAGS & set(item["flags"]))
        if now:
            return "crit", ", ".join(now)
        if item["flags"]:
            return "warn", ", ".join(item["flags"])
        return "ok", ""
    if name == "link":
        if item["value"] != "up":
            return "crit", f"링크 {item['value']}"
        return "ok", ""
    return "ok", ""


def judge(checks, limits):
    """checks 의 각 항목에 level·detail 을 붙이고 전체 level 을 돌려준다."""
    for name, item in checks.items():
        item["level"], item["detail"] = judge_one(name, item, limits)
    return worst(item["level"] for item in checks.values())
