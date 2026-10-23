import json

import pytest

from pimon.led import Led, current_trigger, restore_saved


@pytest.fixture
def state(tmp_path):
    return tmp_path / "state"


def act(pi, name):
    return (pi / "sys/class/leds/ACT" / name).read_text()


def test_current_trigger():
    assert current_trigger("none [mmc0] timer\n") == "mmc0"
    with pytest.raises(ValueError):
        current_trigger("none mmc0\n")


def test_show_and_restore(pi, state):
    led = Led(pi, state_dir=state)
    assert led.original == {"trigger": "mmc0"} and not led.recovered
    assert json.loads((state / "led.json").read_text()) == {"trigger": "mmc0"}
    led.show("crit")
    assert act(pi, "trigger") == "timer\n"
    assert act(pi, "delay_on") == "100\n"
    led.show("ok")
    assert act(pi, "trigger") == "mmc0\n"
    led.show("warn")
    led.restore()
    assert act(pi, "trigger") == "mmc0\n"
    assert not (state / "led.json").exists()


def test_same_level_does_not_rewrite(pi, state):
    led = Led(pi, state_dir=state)
    led.show("warn")
    (pi / "sys/class/leds/ACT/delay_on").write_text("777\n")
    led.show("warn")
    assert act(pi, "delay_on") == "777\n"


def test_restart_after_crash_uses_saved_state(pi, state):
    """되돌리지 못하고 죽은 뒤 다시 시작해도 timer 를 원래 상태로 착각하지 않는다."""
    Led(pi, state_dir=state).show("crit")          # 여기서 죽었다고 치자
    (pi / "sys/class/leds/ACT/trigger").write_text("none mmc0 [timer]\n")
    led = Led(pi, state_dir=state)
    assert led.recovered and led.original == {"trigger": "mmc0"}
    led.restore()
    assert act(pi, "trigger") == "mmc0\n"


def test_restore_saved_after_crash(pi, state):
    Led(pi, state_dir=state).show("crit")
    assert restore_saved(pi, state_dir=state) is True
    assert act(pi, "trigger") == "mmc0\n"
    assert restore_saved(pi, state_dir=state) is False


def test_original_none_keeps_brightness(pi, state):
    (pi / "sys/class/leds/ACT/trigger").write_text("[none] mmc0 timer\n")
    (pi / "sys/class/leds/ACT/brightness").write_text("1\n")
    led = Led(pi, state_dir=state)
    led.show("crit")
    (pi / "sys/class/leds/ACT/brightness").write_text("0\n")
    led.restore()
    assert act(pi, "trigger") == "none\n" and act(pi, "brightness") == "1\n"


def test_failed_show_puts_back_original(pi, state, monkeypatch):
    led = Led(pi, state_dir=state)
    real = led._write

    def broken(name, value):
        if name == "delay_on":
            raise OSError("쓰기 실패")
        real(name, value)
    monkeypatch.setattr(led, "_write", broken)
    with pytest.raises(OSError):
        led.show("crit")
    assert act(pi, "trigger") == "mmc0\n"
    assert led.shown is None


def test_old_name_led0(pi, state):
    (pi / "sys/class/leds/ACT").rename(pi / "sys/class/leds/led0")
    assert Led(pi, state_dir=state).path.name == "led0"


def test_no_led(tmp_path):
    with pytest.raises(FileNotFoundError):
        Led(tmp_path, state_dir=tmp_path / "s")
