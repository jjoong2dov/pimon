"""보드의 ACT LED 로 상태 보여 주기.

정상이면 원래 동작(보통 SD 카드 접근 표시)으로 두고, 주의는 느리게, 경고는 빠르게 깜빡인다.
끝낼 때는 처음 trigger 로 되돌린다.
"""

from pathlib import Path

PATTERNS = {
    "warn": (500, 500),
    "unknown": (500, 500),
    "crit": (100, 100),
}

CANDIDATES = ("ACT", "led0")


def current_trigger(text):
    for word in text.split():
        if word.startswith("[") and word.endswith("]"):
            return word[1:-1]
    raise ValueError(f"trigger 파일에서 현재 값을 못 찾음: {text!r}")


class Led:
    def __init__(self, root="/", name="auto"):
        leds = Path(root) / "sys/class/leds"
        names = CANDIDATES if name == "auto" else (name,)
        for n in names:
            if (leds / n / "trigger").exists():
                self.path = leds / n
                break
        else:
            raise FileNotFoundError(f"LED 를 못 찾음: {leds}/{'|'.join(names)}")
        self.original = current_trigger((self.path / "trigger").read_text())
        self.shown = None

    def _write(self, name, value):
        (self.path / name).write_text(f"{value}\n")

    def show(self, level):
        if level == self.shown:
            return
        if level in PATTERNS:
            on, off = PATTERNS[level]
            self._write("trigger", "timer")
            self._write("delay_on", on)
            self._write("delay_off", off)
        else:
            self._write("trigger", self.original)
        self.shown = level

    def restore(self):
        self._write("trigger", self.original)
        self.shown = None
