"""ACT LED: 시작 때 원래 trigger·brightness 를 저장하고, 판정에 따라 깜빡이고, 끝날 때 복구.

pimon 에서 /sys 에 쓰는 곳은 이 파일 하나뿐이다. 쓰기는 모두 Led._write 를 거친다.

- ok·unknown: 원래 동작으로 둔다.
- warn: 느리게, crit: 빠르게. trigger 를 none 으로 두고 스레드가 brightness 에 0/1 을 쓴다.
- 복구 순서: trigger 를 원래 값으로 쓰고, 원래 trigger 가 none 일 때만 brightness 도 쓴다.
  (실제 커널은 brightness 에 0 을 쓰면 걸려 있던 trigger 를 떼어 버린다.)

강제 종료(SIGKILL·OOM) 대비 기록 파일 (state_path, 기본 /run/pimon/led.json):
- start(): 기록이 있으면 그것을 원래 상태로 쓰고(지금 LED 가 none 이어도), 지금 LED 가
  기록과 다르면 곧바로 기록대로 되돌린다(실패하면 다음 show 가 다시 한다).
  기록이 없으면 지금 상태를 원자적으로 적는다.
  기록이 깨졌으면 경고 후 지금 상태로 다시 적는다.
- 기록을 쓸 수 없으면(디렉터리를 만들어도 안 되면) LedError. LED 는 건드리지 않는다.
  기록 없이 깜빡이다 강제 종료되면 복구할 길이 없기 때문이다.
- stop(): 복구에 성공하면 기록을 지운다. 실패하면 남겨 다음 시작이나 --restore-led 가 다시 한다.
- restore_from_state(): 프로세스 밖(systemd ExecStopPost)에서 기록대로 되돌린다.
기록 파일 경로는 --root 아래로 붙이지 않는다 (/run 은 가짜 파이가 흉내 낼 대상이 아님).

LED 를 읽지 못하면 start() 가 LedError 를 낸다. 원래 상태를 모르면 아무것도 쓰지 않는다.
그 밖의 쓰기 실패는 로그만 남기고 밖으로 내지 않으며, 다음 show(다음 tick)에서 다시 한다.
쓰기 실패 로그는 파일(trigger·brightness)마다 실패가 시작될 때 경고 한 번, 회복될 때 정보 한 번만 남긴다.
"""

import json
import logging
import os
import threading

log = logging.getLogger(__name__)

LED_DIR = "sys/class/leds/ACT"
PERIODS = {"warn": (1.0, 1.0), "crit": (0.15, 0.15)}     # (켜짐 초, 꺼짐 초)


class LedError(Exception):
    pass


class StateError(LedError):
    """기록 파일이 깨졌거나 읽을 수 없음."""


def parse_trigger(text):
    """trigger 파일에서 현재 값을 꺼낸다. 커널은 '... [mmc0] ...', 단어 하나만 있으면 그것."""
    words = text.split()
    marked = [w[1:-1] for w in words if w.startswith("[") and w.endswith("]")]
    if len(marked) == 1 and marked[0]:
        return marked[0]
    if not marked and len(words) == 1:
        return words[0]
    raise ValueError(f"trigger 형식 오류 {text!r}")


# --- 기록 파일 ---

def _valid_state(data):
    if not isinstance(data, dict):
        return False
    trig, bright = data.get("trigger"), data.get("brightness")
    return (isinstance(trig, str) and trig != "" and not any(c.isspace() or c in "[]/" for c in trig)
            and isinstance(bright, str) and bright.isascii() and bright.isdigit())


def load_state(path):
    """기록 파일을 읽어 {"trigger", "brightness"} 를 돌려준다. 없으면 None, 깨졌으면 StateError."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as e:            # UnicodeDecodeError·JSONDecodeError 는 ValueError
        raise StateError(f"{path}: LED 기록을 읽을 수 없음: {e}") from None
    if not _valid_state(data):
        raise StateError(f"{path}: LED 기록 형식 오류: {data!r}")
    return {"trigger": data["trigger"], "brightness": data["brightness"]}


def save_state(path, original):
    """기록 파일을 원자적으로 쓴다 (같은 폴더의 임시 파일 + os.replace). 실패하면 OSError."""
    path = os.fspath(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp.{os.getpid()}"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"trigger": original["trigger"], "brightness": original["brightness"]}, f)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _remove_state(path):
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass
    except OSError as e:
        log.warning("LED 기록 %s 지우기 실패: %s", path, e)


def restore_from_state(root, state_path):
    """기록 파일대로 LED 를 되돌리고 기록을 지운다 (python -m pimon --restore-led).

    기록이 없으면 아무것도 하지 않고 False. 되돌렸으면 True.
    기록이 깨졌으면 아무것도 쓰지 않고 StateError, 쓰기에 실패하면 기록을 남기고 LedError.
    """
    original = load_state(state_path)
    if original is None:
        return False
    led = Led(root, state_path)
    with led._lock:
        led.original = original
        ok = led._restore()
    if not ok:
        raise LedError(f"{led._dir}: LED 를 기록대로 되돌리지 못함 (기록 {state_path} 는 남겨 둠)")
    _remove_state(state_path)
    return True


class Led:
    def __init__(self, root, state_path, periods=PERIODS):
        self._dir = os.path.join(root, LED_DIR)
        self._state_path = os.fspath(state_path)
        self._periods = periods
        self._lock = threading.Lock()
        self._thread = None
        self._halt = None
        self.original = None
        self.mode = "ok"
        self._dirty = False      # LED 가 원래 상태와 다를 수 있음 (복구 실패 포함)
        self._failing = set()    # 지금 쓰기가 실패 중인 파일 이름 (로그를 한 번만 남기려고)

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
        """실패해도 예외를 내지 않는다. 로그는 파일마다 실패가 시작될 때와 회복될 때 한 번씩만.
        같은 파일을 두 스레드가 동시에 쓰는 일은 없으므로(_halt_thread 뒤에 _restore) 잠금은 필요 없다."""
        try:
            self._write(name, text)
        except OSError as e:
            if name not in self._failing:
                self._failing.add(name)
                log.warning("LED %s 쓰기 실패: %s (회복될 때까지 다시 남기지 않음)", name, e)
            return False
        if name in self._failing:
            self._failing.discard(name)
            log.info("LED %s 쓰기 회복", name)
        return True

    def _read_current(self):
        try:
            trigger = parse_trigger(self._read("trigger"))
            bright = self._read("brightness").strip()
            int(bright)
        except (OSError, ValueError) as e:
            raise LedError(f"{self._dir}: LED 를 읽을 수 없음: {e}") from None
        return {"trigger": trigger, "brightness": bright}

    def start(self):
        """원래 상태를 정한다. 기록 파일이 있으면 그것, 없으면 지금 상태를 읽어 기록한다.

        지금 LED 가 기록과 다를 때(앞선 pimon 이 강제 종료된 흔적)만 LED 에 쓴다.
        """
        current = self._read_current()
        try:
            saved = load_state(self._state_path)
        except StateError as e:
            log.warning("%s. 지금 LED 상태로 기록을 다시 씀", e)
            saved = None
        if saved is None:
            try:
                save_state(self._state_path, current)
            except OSError as e:
                raise LedError(f"{self._state_path}: LED 기록을 쓸 수 없음: {e}. "
                               "LED 를 건드리지 않음") from None
            original = current
        else:
            original = saved
        with self._lock:
            self.original = original
            self.mode = "ok"
            self._dirty = False
            if self._differs(current):
                log.warning("LED 가 기록과 다름(%s → %s). 기록대로 되돌림",
                            current["trigger"], original["trigger"])
                self._restore()

    def show(self, level):
        """전체 판정에 맞춰 LED 를 바꾼다. 같은 판정이고 지난번 쓰기가 다 됐으면 아무것도 하지 않는다.

        지난번 쓰기가 실패했으면(복구 실패, trigger 쓰기 실패, 깜빡임 스레드가 끝남) 같은 판정이어도
        다시 한다. show 는 tick 마다 불리므로 일시 실패는 다음 tick 에 풀린다.
        """
        mode = level if level in self._periods else "ok"
        with self._lock:
            if self.original is None or (mode == self.mode and self._settled()):
                return
            blinking = self.mode != "ok" and self._settled()    # trigger 가 이미 none
            self._halt_thread()
            if mode == "ok":
                self._restore()
            else:
                self._blink(mode, set_trigger=not blinking)
            self.mode = mode

    def stop(self):
        """깜빡임을 멈추고 원래 상태로 되돌린다. 성공하면 기록을 지운다. 여러 번 불러도 된다."""
        with self._lock:
            if self.original is None:
                return
            self._halt_thread()
            ok = self._restore() if self.mode != "ok" or self._dirty else True
            if ok:
                _remove_state(self._state_path)
            self.mode = "ok"
            self.original = None
            self._dirty = False

    # 아래는 잠금을 쥔 채로 부른다.

    def _differs(self, current):
        if current["trigger"] != self.original["trigger"]:
            return True
        return self.original["trigger"] == "none" and current["brightness"] != self.original["brightness"]

    def _settled(self):
        """지금 mode 대로 LED 가 되어 있다고 볼 수 있으면 True."""
        if self.mode == "ok":
            return not self._dirty
        return self._thread is not None and self._thread.is_alive()

    def _blink(self, mode, set_trigger):
        """trigger 를 none 으로 쓴 뒤 깜빡임 스레드를 띄운다. trigger 쓰기가 실패하면 스레드 없이
        돌아가고, _settled() 가 False 라 다음 show 가 다시 한다."""
        self._dirty = True
        if set_trigger and not self._try_write("trigger", "none"):
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
        """원래 상태로 되돌린다. 필요한 쓰기가 모두 되면 True."""
        ok = self._try_write("trigger", self.original["trigger"])
        if ok and self.original["trigger"] == "none":
            ok = self._try_write("brightness", self.original["brightness"])
        self._dirty = not ok
        return ok
