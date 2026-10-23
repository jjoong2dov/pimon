"""보드의 ACT LED 로 상태 보여 주기.

정상이면 원래 동작(보통 SD 카드 접근 표시)으로 두고, 주의는 느리게, 경고는 빠르게 깜빡인다.

처음 LED 상태는 state_dir 의 led.json 에 적어 둔다. pimon 이 LED 를 되돌리지 못하고 죽은 뒤
다시 시작해도, 그때의 깜빡임을 "원래 상태"로 착각하지 않고 이 파일의 값을 쓴다.
깨끗하게 되돌리면 파일을 지운다. systemd 의 ExecStopPost 가 `pimon restore-led` 로 같은 일을 한다.
"""

import json
import os
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


def find(root="/", name="auto"):
    leds = Path(root) / "sys/class/leds"
    names = CANDIDATES if name == "auto" else (name,)
    for n in names:
        if (leds / n / "trigger").exists():
            return leds / n
    raise FileNotFoundError(f"LED 를 못 찾음: {leds}/{'|'.join(names)}")


def _read_state(path):
    """trigger 와, 그 trigger 가 쓰는 값(timer 의 delay, none 의 brightness)."""
    state = {"trigger": current_trigger((path / "trigger").read_text())}
    if state["trigger"] == "timer":
        for name in ("delay_on", "delay_off"):
            state[name] = (path / name).read_text().strip()
    elif state["trigger"] == "none":
        state["brightness"] = (path / "brightness").read_text().strip()
    return state


KEYS = {"trigger", "delay_on", "delay_off", "brightness"}


def _load_state(state_file):
    """led.json 을 읽고 모양을 검사한다. 이상하면 ValueError."""
    try:
        data = json.loads(state_file.read_text())
    except ValueError as e:
        raise ValueError(f"{state_file}: JSON 이 아님: {e}") from None
    if not isinstance(data, dict) or not isinstance(data.get("led"), str) \
            or not isinstance(data.get("state"), dict):
        raise ValueError(f"{state_file}: 모양이 이상함")
    state = data["state"]
    if not isinstance(state.get("trigger"), str) or not set(state) <= KEYS \
            or not all(isinstance(state[k], str) and state[k].isdigit() for k in state if k != "trigger"):
        raise ValueError(f"{state_file}: LED 상태 값이 이상함: {state!r}")
    if "/" in data["led"] or data["led"] in (".", ".."):
        raise ValueError(f"{state_file}: LED 이름이 이상함: {data['led']!r}")
    return data["led"], state


def _apply_state(path, state):
    (path / "trigger").write_text(f"{state['trigger']}\n")
    for name in ("delay_on", "delay_off", "brightness"):
        if name in state:
            (path / name).write_text(f"{state[name]}\n")


class Led:
    def __init__(self, root="/", name="auto", state_dir="/var/lib/pimon"):
        self.state_file = Path(state_dir) / "led.json"
        if self.state_file.exists():
            # 기록에 적힌 LED 를 쓴다. 그사이 설정의 LED 이름이 바뀌었어도 처음 LED 를 되돌린다.
            saved_name, self.original = _load_state(self.state_file)
            self.path = find(root, saved_name)
            self.recovered = True
        else:
            self.path = find(root, name)
            self.original = _read_state(self.path)
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.state_file.with_suffix(".tmp")
            tmp.write_text(json.dumps({"led": self.path.name, "state": self.original}))
            os.replace(tmp, self.state_file)
            self.recovered = False
        self.shown = None

    def _write(self, name, value):
        (self.path / name).write_text(f"{value}\n")

    def show(self, level):
        if level == self.shown:
            return
        self.shown = None
        try:
            if level in PATTERNS:
                on, off = PATTERNS[level]
                self._write("trigger", "timer")
                self._write("delay_on", on)
                self._write("delay_off", off)
            else:
                _apply_state(self.path, self.original)
        except OSError:
            # 반쯤 바뀐 채로 두지 않는다
            try:
                _apply_state(self.path, self.original)
            except OSError:
                pass
            raise
        self.shown = level

    def restore(self):
        _apply_state(self.path, self.original)
        self.state_file.unlink(missing_ok=True)
        self.shown = None


def restore_saved(root="/", state_dir="/var/lib/pimon"):
    """led.json 이 남아 있으면 거기 적힌 LED 를 그 상태로 되돌린다. 되돌렸으면 True."""
    state_file = Path(state_dir) / "led.json"
    if not state_file.exists():
        return False
    name, state = _load_state(state_file)
    _apply_state(find(root, name), state)
    state_file.unlink()
    return True
