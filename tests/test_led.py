import json
import logging
import os
import time
from pathlib import Path

import pytest

from fake.make_fake import led_trigger_text, make
from pimon import led as led_mod
from pimon.led import Led, LedError, restore_from_state

LED = "sys/class/leds/ACT"
FAST = {"warn": (0.02, 0.02), "crit": (0.005, 0.005)}


@pytest.fixture(autouse=True)
def only_tmp_writes(tmp_path, monkeypatch):
    """LED 쓰기·기록 파일 쓰기는 모두 시험용 임시 폴더 안이어야 한다.
    이 PC 의 진짜 /sys 나 /run 에 쓰면 바로 실패."""
    real = led_mod.Led._write
    real_save = led_mod.save_state
    writes = []

    def guarded(self, name, text):
        path = Path(self._dir, name).resolve()
        assert path.is_relative_to(tmp_path.resolve()), f"시험 폴더 밖에 쓰려 함: {path}"
        writes.append((name, text))
        return real(self, name, text)

    def guarded_save(path, original):
        assert Path(path).resolve().is_relative_to(tmp_path.resolve()), f"시험 폴더 밖에 기록: {path}"
        return real_save(path, original)

    monkeypatch.setattr(led_mod.Led, "_write", guarded)
    monkeypatch.setattr(led_mod, "save_state", guarded_save)
    return writes


def state_path(root):
    """가짜 파이 폴더(tmp_path/pi) 옆의 tmp_path/run/pimon/led.json. 가짜 폴더 비교에 섞이지 않게 밖에 둔다."""
    return Path(root).parent / "run" / "pimon" / "led.json"


def new_led(root, periods=FAST):
    return Led(root, state_path(root), periods=periods)


def trigger(root):
    return led_mod.parse_trigger((root / LED / "trigger").read_text())


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
    led = new_led(fake_pi)
    led.start()
    assert led.original == {"trigger": "mmc0", "brightness": "0"}
    assert only_tmp_writes == []              # 시작만으로는 LED 에 아무것도 쓰지 않는다


def test_start_without_led_raises_and_writes_nothing(fake_pi, only_tmp_writes):
    (fake_pi / LED / "trigger").unlink()
    with pytest.raises(LedError):
        new_led(fake_pi).start()
    assert only_tmp_writes == []
    assert not state_path(fake_pi).exists()


def test_start_with_garbage_trigger_raises_and_writes_nothing(fake_pi, only_tmp_writes):
    (fake_pi / LED / "trigger").write_text("??? ???\n")
    with pytest.raises(LedError):
        new_led(fake_pi).start()
    assert only_tmp_writes == []
    assert not state_path(fake_pi).exists()


def test_show_before_start_does_nothing(fake_pi, only_tmp_writes):
    led = new_led(fake_pi)
    led.show("crit")
    led.stop()
    assert only_tmp_writes == []


# --- 판정별 동작 ---

@pytest.mark.parametrize("level", ["ok", "unknown"])
def test_ok_and_unknown_leave_led_alone(fake_pi, only_tmp_writes, level):
    before = snapshot(fake_pi)
    with new_led(fake_pi) as led:
        led.show(level)
        time.sleep(0.05)
    assert only_tmp_writes == []
    assert snapshot(fake_pi) == before


@pytest.mark.parametrize("level", ["warn", "crit"])
def test_warn_and_crit_blink(fake_pi, only_tmp_writes, level):
    with new_led(fake_pi) as led:
        led.show(level)
        assert trigger(fake_pi) == "none"
        seen = set()
        assert wait_for(lambda: seen.add(brightness(fake_pi)) or seen >= {"0", "1"})
        assert led.mode == level
    assert only_tmp_writes[0] == ("trigger", "none")


def test_crit_blinks_faster_than_warn(fake_pi, only_tmp_writes):
    def count(level):
        only_tmp_writes.clear()
        with new_led(fake_pi, periods={"warn": (0.05, 0.05), "crit": (0.005, 0.005)}) as led:
            led.show(level)
            time.sleep(0.2)
        return sum(1 for n, _ in only_tmp_writes if n == "brightness")
    assert count("crit") > count("warn") * 2


def test_same_level_again_does_not_restart(fake_pi, only_tmp_writes):
    with new_led(fake_pi) as led:
        led.show("warn")
        t = led._thread
        led.show("warn")
        assert led._thread is t


def test_back_to_ok_restores(fake_pi):
    with new_led(fake_pi) as led:
        led.show("crit")
        time.sleep(0.03)
        led.show("ok")
        assert led.mode == "ok"
        assert trigger(fake_pi) == "mmc0"
        time.sleep(0.03)                       # 멈춘 스레드가 뒤늦게 brightness 를 쓰지 않는지
        assert trigger(fake_pi) == "mmc0"


def test_switch_warn_to_crit(fake_pi):
    with new_led(fake_pi) as led:
        led.show("warn")
        led.show("crit")
        assert led.mode == "crit"
        assert sum(t.name == "pimon-led" for t in __import__("threading").enumerate()) == 1


# --- 복구 ---

def test_stop_restores_trigger_and_stops_thread(fake_pi):
    led = new_led(fake_pi)
    led.start()
    led.show("crit")
    time.sleep(0.03)
    led.stop()
    assert trigger(fake_pi) == "mmc0"
    assert led._thread is None
    after = snapshot(fake_pi)
    time.sleep(0.03)
    assert snapshot(fake_pi) == after          # stop 뒤에는 아무도 쓰지 않는다


def test_restore_does_not_write_brightness_when_trigger_was_not_none(fake_pi, only_tmp_writes):
    """실제 커널은 brightness 에 0 을 쓰면 trigger 를 none 으로 바꿔 버린다.
    원래 trigger 가 mmc0 이면 마지막 쓰기는 trigger 여야 한다."""
    with new_led(fake_pi) as led:
        led.show("crit")
        time.sleep(0.03)
    assert only_tmp_writes[-1] == ("trigger", "mmc0")


def test_restore_writes_brightness_when_trigger_was_none(fake_pi, only_tmp_writes):
    (fake_pi / LED / "trigger").write_text(led_trigger_text("none"))
    (fake_pi / LED / "brightness").write_text("1\n")
    with new_led(fake_pi) as led:
        led.show("crit")
        time.sleep(0.03)
    assert only_tmp_writes[-2:] == [("trigger", "none"), ("brightness", "1")]
    assert brightness(fake_pi) == "1"


def test_stop_twice_is_fine(fake_pi, only_tmp_writes):
    led = new_led(fake_pi)
    led.start()
    led.show("warn")
    led.stop()
    n = len(only_tmp_writes)
    led.stop()
    assert len(only_tmp_writes) == n


def test_exception_inside_with_restores(fake_pi):
    with pytest.raises(RuntimeError):
        with new_led(fake_pi) as led:
            led.show("crit")
            raise RuntimeError("tick 에서 터짐")
    assert trigger(fake_pi) == "mmc0"


def test_ctrl_c_inside_with_restores(fake_pi):
    with pytest.raises(KeyboardInterrupt):
        with new_led(fake_pi) as led:
            led.show("warn")
            raise KeyboardInterrupt
    assert trigger(fake_pi) == "mmc0"


def test_only_led_files_change(fake_pi):
    """실행 전후 가짜 폴더 전체를 비교: LED 두 파일 말고는 그대로."""
    before = snapshot(fake_pi)
    with new_led(fake_pi) as led:
        for level in ["warn", "crit", "ok", "crit"]:
            led.show(level)
            time.sleep(0.01)
    after = snapshot(fake_pi)
    changed = {p.relative_to(fake_pi).as_posix() for p in before if before[p] != after[p]}
    assert changed <= {f"{LED}/trigger", f"{LED}/brightness"}
    assert set(after) == set(before)


def test_write_error_while_blinking_does_not_raise(fake_pi):
    led = new_led(fake_pi)
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
    assert trigger(fake_pi) == "mmc0"


# --- 기록 파일 (강제 종료 대비) ---

def read_record(root):
    return json.loads(state_path(root).read_text())


def kill_while_blinking(root):
    """강제 종료 흉내: 깜빡이던 중 복구 없이 스레드만 멈춤. trigger 는 none 으로 남는다."""
    first = new_led(root)
    first.start()
    first.show("crit")
    first._halt_thread()
    assert trigger(root) == "none"


def test_start_writes_record(fake_pi):
    led = new_led(fake_pi)
    led.start()
    rec = read_record(fake_pi)
    assert (rec["trigger"], rec["brightness"]) == ("mmc0", "0")
    assert os.listdir(state_path(fake_pi).parent) == ["led.json"]     # 임시 파일이 남지 않음
    led.stop()


def test_start_creates_record_directory(fake_pi):
    assert not state_path(fake_pi).parent.exists()
    with new_led(fake_pi):
        assert state_path(fake_pi).exists()


@pytest.mark.parametrize("level", ["ok", "crit"])
def test_stop_removes_record(fake_pi, level):
    with new_led(fake_pi) as led:
        led.show(level)
        time.sleep(0.01)
    assert not state_path(fake_pi).exists()


def test_record_kept_when_restore_fails(fake_pi):
    def blocked(name, text):
        raise PermissionError("막힘")

    led = new_led(fake_pi)
    led.start()
    led.show("crit")
    led._halt_thread()
    led._write = blocked
    led.stop()
    assert state_path(fake_pi).exists()         # 다음 시작이나 --restore-led 가 다시 시도


def test_restart_after_kill_restores_original(fake_pi):
    kill_while_blinking(fake_pi)
    second = new_led(fake_pi)
    second.start()
    assert second.original == {"trigger": "mmc0", "brightness": "0"}
    assert trigger(fake_pi) == "mmc0"          # 시작하자마자 남은 흔적을 원래대로
    assert read_record(fake_pi)["trigger"] == "mmc0"
    second.stop()
    assert trigger(fake_pi) == "mmc0"
    assert not state_path(fake_pi).exists()


def test_start_with_record_matching_led_writes_nothing(fake_pi, only_tmp_writes):
    led_mod.save_state(state_path(fake_pi), {"trigger": "mmc0", "brightness": "0"})
    with new_led(fake_pi) as led:
        assert led.original["trigger"] == "mmc0"
    assert only_tmp_writes == []


@pytest.mark.parametrize("text", [
    "",
    "{깨짐",
    "[]",
    '{"trigger": "mmc0"}',
    '{"trigger": "", "brightness": "0"}',
    '{"trigger": "mmc0 none", "brightness": "0"}',
    '{"trigger": "[mmc0]", "brightness": "0"}',
    '{"trigger": "mmc0", "brightness": 0}',
    '{"trigger": "mmc0", "brightness": "x"}',
])
def test_start_with_broken_record_warns_and_rewrites(fake_pi, caplog, only_tmp_writes, text):
    p = state_path(fake_pi)
    p.parent.mkdir(parents=True)
    p.write_text(text)
    with caplog.at_level(logging.WARNING, logger="pimon.led"):
        led = new_led(fake_pi)
        led.start()
    assert "기록" in caplog.text
    assert led.original == {"trigger": "mmc0", "brightness": "0"}
    assert read_record(fake_pi)["trigger"] == "mmc0"
    assert only_tmp_writes == []
    led.stop()


def test_record_unwritable_raises_and_leaves_led_alone(fake_pi, tmp_path, only_tmp_writes):
    (tmp_path / "파일").write_text("디렉터리 자리에 파일\n")
    led = Led(fake_pi, tmp_path / "파일" / "led.json", periods=FAST)
    with pytest.raises(LedError):
        led.start()
    led.show("crit")                            # 시작 실패 뒤에는 아무것도 하지 않는다
    led.stop()
    assert only_tmp_writes == []


def test_record_replace_failure_leaves_no_temp_file(fake_pi, monkeypatch, only_tmp_writes):
    def boom(src, dst):
        raise OSError("replace 실패")
    monkeypatch.setattr(led_mod.os, "replace", boom)
    with pytest.raises(LedError):
        new_led(fake_pi).start()
    assert os.listdir(state_path(fake_pi).parent) == []
    assert only_tmp_writes == []


# --- restore_from_state (python -m pimon --restore-led) ---

def test_restore_from_state_without_record_does_nothing(fake_pi, only_tmp_writes):
    assert restore_from_state(fake_pi, state_path(fake_pi)) is False
    assert only_tmp_writes == []


def test_restore_from_state_after_kill(fake_pi, only_tmp_writes):
    kill_while_blinking(fake_pi)
    only_tmp_writes.clear()
    assert restore_from_state(fake_pi, state_path(fake_pi)) is True
    assert only_tmp_writes == [("trigger", "mmc0")]       # 원래 trigger 가 none 이 아니면 brightness 안 씀
    assert not state_path(fake_pi).exists()


def test_restore_from_state_uses_recorded_trigger_not_mmc0(fake_pi, only_tmp_writes):
    (fake_pi / LED / "trigger").write_text(led_trigger_text("heartbeat"))
    kill_while_blinking(fake_pi)
    restore_from_state(fake_pi, state_path(fake_pi))
    assert trigger(fake_pi) == "heartbeat"


def test_restore_from_state_writes_brightness_when_original_none(fake_pi, only_tmp_writes):
    (fake_pi / LED / "trigger").write_text(led_trigger_text("none"))
    (fake_pi / LED / "brightness").write_text("1\n")
    kill_while_blinking(fake_pi)
    only_tmp_writes.clear()
    restore_from_state(fake_pi, state_path(fake_pi))
    assert only_tmp_writes == [("trigger", "none"), ("brightness", "1")]


def test_restore_from_state_broken_record_writes_nothing(fake_pi, only_tmp_writes):
    p = state_path(fake_pi)
    p.parent.mkdir(parents=True)
    p.write_text("{깨짐")
    with pytest.raises(LedError):
        restore_from_state(fake_pi, p)
    assert only_tmp_writes == []
    assert p.exists()                           # 사람이 볼 수 있게 남겨 둔다


def test_restore_from_state_write_failure_keeps_record(fake_pi):
    kill_while_blinking(fake_pi)
    (fake_pi / LED / "trigger").unlink()
    (fake_pi / LED / "trigger").mkdir()         # 쓸 수 없게
    with pytest.raises(LedError):
        restore_from_state(fake_pi, state_path(fake_pi))
    assert state_path(fake_pi).exists()
