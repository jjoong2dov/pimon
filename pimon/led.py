"""ACT LED: 시작 때 원래 trigger·brightness 를 저장하고, 판정에 따라 깜빡이고, 끝날 때 복구.

pimon 에서 /sys 에 쓰는 곳은 이 파일 하나뿐이다. 쓰기는 모두 Led._write 를 거친다.

- ok·unknown: 원래 동작으로 둔다.
- warn: 느리게, crit: 빠르게. trigger 를 none 으로 두고 스레드가 brightness 에 0/1 을 쓴다.
- 복구 순서: trigger 를 원래 값으로 쓰고, 원래 trigger 가 none 일 때만 brightness 도 쓴다.
  (실제 커널은 brightness 에 0 을 쓰면 걸려 있던 trigger 를 떼어 버린다.)

LED 를 읽지 못하면 start() 가 LedError 를 낸다. 원래 상태를 모르면 아무것도 쓰지 않는다.
그 밖의 쓰기 실패는 로그만 남기고 밖으로 내지 않는다.
"""

import logging
import os
import threading

log = logging.getLogger(__name__)

LED_DIR = "sys/class/leds/ACT"
PERIODS = {"warn": (1.0, 1.0), "crit": (0.15, 0.15)}     # (켜짐 초, 꺼짐 초)


class LedError(Exception):
    pass


def parse_trigger(text):
    """trigger 파일에서 현재 값을 꺼낸다. 커널은 '... [mmc0] ...', 단어 하나만 있으면 그것."""
    words = text.split()
    marked = [w[1:-1] for w in words if w.startswith("[") and w.endswith("]")]
    if len(marked) == 1 and marked[0]:
        return marked[0]
    if not marked and len(words) == 1:
        return words[0]
    raise ValueError(f"trigger 형식 오류 {text!r}")


class Led:
    def __init__(self, root, periods=PERIODS):
        self._dir = os.path.join(root, LED_DIR)
        self._periods = periods
        self._lock = threading.Lock()
        self._thread = None
        self._halt = None
        self.original = None
        self.mode = "ok"

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *exc):
        self.stop()
        return False

    def _read(self, name):
        with open(os.path.join(self._dir, name)) as f:
            return f.read()

    def _write(self, name, text):
        with open(os.path.join(self._dir, name), "w") as f:
            f.write(text)

    def _try_write(self, name, text):
        try:
            self._write(name, text)
            return True
        except OSError as e:
            log.warning("LED %s 쓰기 실패: %s", name, e)
            return False

    def start(self):
        """원래 상태를 읽어 둔다. 아무것도 쓰지 않는다."""
        try:
            trigger = parse_trigger(self._read("trigger"))
            bright = self._read("brightness").strip()
            int(bright)
        except (OSError, ValueError) as e:
            raise LedError(f"{self._dir}: LED 를 읽을 수 없음: {e}") from None
        with self._lock:
            self.original = {"trigger": trigger, "brightness": bright}
            self.mode = "ok"

    def show(self, level):
        """전체 판정에 맞춰 LED 를 바꾼다. 같은 판정이면 아무것도 하지 않는다."""
        mode = level if level in self._periods else "ok"
        with self._lock:
            if self.original is None or mode == self.mode:
                return
            self._halt_thread()
            if mode == "ok":
                self._restore()
            else:
                self._blink(mode)
            self.mode = mode

    def stop(self):
        """깜빡임을 멈추고 원래 상태로 되돌린다. 여러 번 불러도 된다."""
        with self._lock:
            if self.original is None:
                return
            self._halt_thread()
            if self.mode != "ok":
                self._restore()
            self.mode = "ok"
            self.original = None

    # 아래는 잠금을 쥔 채로 부른다.

    def _blink(self, mode):
        if self.mode == "ok" and not self._try_write("trigger", "none"):
            return
        on, off = self._periods[mode]
        halt = threading.Event()
        t = threading.Thread(target=self._blink_loop, args=(halt, on, off),
                             name="pimon-led", daemon=True)
        self._halt, self._thread = halt, t
        t.start()

    def _blink_loop(self, halt, on, off):
        while not halt.is_set():
            for value, secs in (("1", on), ("0", off)):
                if halt.is_set() or not self._try_write("brightness", value):
                    return
                if halt.wait(secs):
                    return

    def _halt_thread(self):
        if self._thread is not None:
            self._halt.set()
            self._thread.join()
            self._thread = self._halt = None

    def _restore(self):
        self._try_write("trigger", self.original["trigger"])
        if self.original["trigger"] == "none":
            self._try_write("brightness", self.original["brightness"])
