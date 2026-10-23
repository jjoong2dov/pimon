import time
from pathlib import Path

import pytest

from fake.make_fake import led_trigger_text, make
from pimon import led as led_mod
from pimon.led import Led, LedError

LED = "sys/class/leds/ACT"
FAST = {"warn": (0.02, 0.02), "crit": (0.005, 0.005)}


@pytest.fixture(autouse=True)
def only_tmp_writes(tmp_path, monkeypatch):
    """LED 쓰기는 모두 시험용 임시 폴더 안이어야 한다. 이 PC 의 진짜 /sys 에 쓰면 바로 실패."""
    real = led_mod.Led._write
    writes = []

    def guarded(self, name, text):
        path = Path(self._dir, name).resolve()
        assert path.is_relative_to(tmp_path.resolve()), f"시험 폴더 밖에 쓰려 함: {path}"
        writes.append((name, text))
        return real(self, name, text)

    monkeypatch.setattr(led_mod.Led, "_write", guarded)
    return writes


def trigger(root):
    return (root / LED / "trigger").read_text()


def brightness(root):
    return (root / LED / "brightness").read_text().strip()


def snapshot(root):
    return {p: p.read_bytes() for p in Path(root).rglob("*") if p.is_file()}


def wait_for(cond, timeout=2.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.002)
    return False


# --- 읽기 ---

@pytest.mark.parametrize("text, want", [
    (led_trigger_text("mmc0"), "mmc0"),
    (led_trigger_text("none"), "none"),
    (led_trigger_text("heartbeat"), "heartbeat"),
    ("none\n", "none"),                       # 가짜 폴더에 우리가 쓴 뒤의 모양
])
def test_parse_trigger(text, want):
    assert led_mod.parse_trigger(text) == want


@pytest.mark.parametrize("text", ["", "none mmc0\n", "[none] [mmc0]\n"])
def test_parse_trigger_bad(text):
    with pytest.raises(ValueError):
        led_mod.parse_trigger(text)


def test_start_saves_original(fake_pi, only_tmp_writes):
    led = Led(fake_pi, periods=FAST)
    led.start()
    assert led.original == {"trigger": "mmc0", "brightness": "0"}
    assert only_tmp_writes == []              # 시작만으로는 아무것도 쓰지 않는다


def test_start_without_led_raises_and_writes_nothing(fake_pi, only_tmp_writes):
    (fake_pi / LED / "trigger").unlink()
    with pytest.raises(LedError):
        Led(fake_pi, periods=FAST).start()
    assert only_tmp_writes == []


def test_start_with_garbage_trigger_raises_and_writes_nothing(fake_pi, only_tmp_writes):
    (fake_pi / LED / "trigger").write_text("??? ???\n")
    with pytest.raises(LedError):
        Led(fake_pi, periods=FAST).start()
    assert only_tmp_writes == []


def test_show_before_start_does_nothing(fake_pi, only_tmp_writes):
    led = Led(fake_pi, periods=FAST)
    led.show("crit")
    led.stop()
    assert only_tmp_writes == []


# --- 판정별 동작 ---

@pytest.mark.parametrize("level", ["ok", "unknown"])
def test_ok_and_unknown_leave_led_alone(fake_pi, only_tmp_writes, level):
    before = snapshot(fake_pi)
    with Led(fake_pi, periods=FAST) as led:
        led.show(level)
        time.sleep(0.05)
    assert only_tmp_writes == []
    assert snapshot(fake_pi) == before


@pytest.mark.parametrize("level", ["warn", "crit"])
def test_warn_and_crit_blink(fake_pi, only_tmp_writes, level):
    with Led(fake_pi, periods=FAST) as led:
        led.show(level)
        assert trigger(fake_pi).strip() == "none"
        seen = set()
        assert wait_for(lambda: seen.add(brightness(fake_pi)) or seen >= {"0", "1"})
        assert led.mode == level
    assert only_tmp_writes[0] == ("trigger", "none")


def test_crit_blinks_faster_than_warn(fake_pi, only_tmp_writes):
    def count(level):
        only_tmp_writes.clear()
        with Led(fake_pi, periods={"warn": (0.05, 0.05), "crit": (0.005, 0.005)}) as led:
            led.show(level)
            time.sleep(0.2)
        return sum(1 for n, _ in only_tmp_writes if n == "brightness")
    assert count("crit") > count("warn") * 2


def test_same_level_again_does_not_restart(fake_pi, only_tmp_writes):
    with Led(fake_pi, periods=FAST) as led:
        led.show("warn")
        t = led._thread
        led.show("warn")
        assert led._thread is t


def test_back_to_ok_restores(fake_pi):
    with Led(fake_pi, periods=FAST) as led:
        led.show("crit")
        time.sleep(0.03)
        led.show("ok")
        assert led.mode == "ok"
        assert trigger(fake_pi).strip() == "mmc0"
        time.sleep(0.03)                       # 멈춘 스레드가 뒤늦게 brightness 를 쓰지 않는지
        assert trigger(fake_pi).strip() == "mmc0"


def test_switch_warn_to_crit(fake_pi):
    with Led(fake_pi, periods=FAST) as led:
        led.show("warn")
        led.show("crit")
        assert led.mode == "crit"
        assert sum(t.name == "pimon-led" for t in __import__("threading").enumerate()) == 1


# --- 복구 ---

def test_stop_restores_trigger_and_stops_thread(fake_pi):
    led = Led(fake_pi, periods=FAST)
    led.start()
    led.show("crit")
    time.sleep(0.03)
    led.stop()
    assert trigger(fake_pi).strip() == "mmc0"
    assert led._thread is None
    after = snapshot(fake_pi)
    time.sleep(0.03)
    assert snapshot(fake_pi) == after          # stop 뒤에는 아무도 쓰지 않는다


def test_restore_does_not_write_brightness_when_trigger_was_not_none(fake_pi, only_tmp_writes):
    """실제 커널은 brightness 에 0 을 쓰면 trigger 를 none 으로 바꿔 버린다.
    원래 trigger 가 mmc0 이면 마지막 쓰기는 trigger 여야 한다."""
    with Led(fake_pi, periods=FAST) as led:
        led.show("crit")
        time.sleep(0.03)
    assert only_tmp_writes[-1] == ("trigger", "mmc0")


def test_restore_writes_brightness_when_trigger_was_none(fake_pi, only_tmp_writes):
    (fake_pi / LED / "trigger").write_text(led_trigger_text("none"))
    (fake_pi / LED / "brightness").write_text("1\n")
    with Led(fake_pi, periods=FAST) as led:
        led.show("crit")
        time.sleep(0.03)
    assert only_tmp_writes[-2:] == [("trigger", "none"), ("brightness", "1")]
    assert brightness(fake_pi) == "1"


def test_stop_twice_is_fine(fake_pi, only_tmp_writes):
    led = Led(fake_pi, periods=FAST)
    led.start()
    led.show("warn")
    led.stop()
    n = len(only_tmp_writes)
    led.stop()
    assert len(only_tmp_writes) == n


def test_exception_inside_with_restores(fake_pi):
    with pytest.raises(RuntimeError):
        with Led(fake_pi, periods=FAST) as led:
            led.show("crit")
            raise RuntimeError("tick 에서 터짐")
    assert trigger(fake_pi).strip() == "mmc0"


def test_ctrl_c_inside_with_restores(fake_pi):
    with pytest.raises(KeyboardInterrupt):
        with Led(fake_pi, periods=FAST) as led:
            led.show("warn")
            raise KeyboardInterrupt
    assert trigger(fake_pi).strip() == "mmc0"


def test_only_led_files_change(fake_pi):
    """실행 전후 가짜 폴더 전체를 비교: LED 두 파일 말고는 그대로."""
    before = snapshot(fake_pi)
    with Led(fake_pi, periods=FAST) as led:
        for level in ["warn", "crit", "ok", "crit"]:
            led.show(level)
            time.sleep(0.01)
    after = snapshot(fake_pi)
    changed = {p.relative_to(fake_pi).as_posix() for p in before if before[p] != after[p]}
    assert changed <= {f"{LED}/trigger", f"{LED}/brightness"}
    assert set(after) == set(before)


def test_write_error_while_blinking_does_not_raise(fake_pi):
    led = Led(fake_pi, periods=FAST)
    real = led._write

    def failing(name, text):
        if name == "brightness":
            raise PermissionError("쓰기 막힘")
        real(name, text)

    led._write = failing
    led.start()
    led.show("crit")
    time.sleep(0.03)
    assert not led._thread.is_alive()           # 스레드는 조용히 끝난다
    led.stop()                                  # 예외 없이 끝나고 trigger 는 복구
    assert trigger(fake_pi).strip() == "mmc0"


# --- 강제 종료 뒤 다시 시작: 지금 구조로는 복구 못 함 (보고용) ---

@pytest.mark.xfail(strict=True, reason="SIGKILL 등으로 stop() 없이 죽으면 trigger 가 none 으로 남고, "
                                       "다시 시작한 pimon 은 none 을 '원래 값'으로 저장한다")
def test_restart_after_kill_restores_original(fake_pi):
    first = Led(fake_pi, periods=FAST)
    first.start()
    first.show("crit")
    first._halt_thread()                        # 강제 종료 흉내: 복구 없이 스레드만 멈춤
    second = Led(fake_pi, periods=FAST)
    second.start()
    second.stop()
    assert trigger(fake_pi).strip() == "mmc0"
