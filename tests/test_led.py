import pytest

from pimon.led import Led, current_trigger


def trig(pi):
    return current_trigger((pi / "sys/class/leds/ACT/trigger").read_text())


def test_current_trigger():
    assert current_trigger("none [mmc0] timer\n") == "mmc0"
    with pytest.raises(ValueError):
        current_trigger("none mmc0\n")


def test_show_and_restore(pi):
    led = Led(pi)
    assert led.original == "mmc0"
    led.show("crit")
    d = pi / "sys/class/leds/ACT"
    assert (d / "trigger").read_text() == "timer\n"
    assert (d / "delay_on").read_text() == "100\n"
    led.show("ok")
    assert (d / "trigger").read_text() == "mmc0\n"
    led.show("warn")
    led.restore()
    assert (d / "trigger").read_text() == "mmc0\n"


def test_same_level_does_not_rewrite(pi):
    led = Led(pi)
    led.show("warn")
    (pi / "sys/class/leds/ACT/delay_on").write_text("777\n")
    led.show("warn")
    assert (pi / "sys/class/leds/ACT/delay_on").read_text() == "777\n"


def test_old_name_led0(pi):
    (pi / "sys/class/leds/ACT").rename(pi / "sys/class/leds/led0")
    assert Led(pi).path.name == "led0"


def test_no_led(tmp_path):
    with pytest.raises(FileNotFoundError):
        Led(tmp_path)
