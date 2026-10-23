"""Monitor: tick() = 수집 → 판정 → 현재 상태 저장, run() = 주기 반복, stop().

현재 상태는 잠금 아래에 두고, status() 는 복사본을 돌려준다 (web 스레드가 읽어도 안전).
"""

import copy
import logging
import os
import threading
import time as _time
from datetime import datetime

from pimon.collect import collect_all
from pimon.judge import judge_all

log = logging.getLogger(__name__)


def _now():
    return datetime.now().astimezone()


class Monitor:
    def __init__(self, root, cfg, statvfs=os.statvfs, now=_now, clock=_time.monotonic):
        self.root = root
        self.cfg = cfg
        self._statvfs = statvfs
        self._now = now
        self._clock = clock
        self._lock = threading.Lock()
        self._status = None
        self._stop = threading.Event()

    def tick(self):
        """한 번 수집·판정하고 현재 상태로 저장한 뒤 그 복사본을 돌려준다."""
        time = self._now().isoformat(timespec="seconds")
        judged = judge_all(collect_all(self.root, self.cfg, statvfs=self._statvfs), self.cfg)
        status = {"time": time, **judged}
        with self._lock:
            self._status = status
        return copy.deepcopy(status)

    def status(self):
        """마지막 tick() 결과의 복사본. 아직 한 번도 안 돌았으면 None."""
        with self._lock:
            return copy.deepcopy(self._status)

    def stop(self):
        """run() 을 멈춘다. 어느 스레드에서 불러도 되고, 대기 중이면 곧바로 깨운다."""
        self._stop.set()

    def run(self, on_status=None, wait=None):
        """stop() 이 불릴 때까지 interval_s 마다 tick() 한다. 부른 스레드에서 돈다.

        - 첫 tick 은 곧바로. 그 뒤로는 시작 시각 + k × interval_s 칸에 맞춘다 (tick 시간만큼 밀리지 않음).
          tick 이 간격보다 오래 걸려 칸을 놓치면 몰아서 돌지 않고 다음 칸까지 기다린다.
        - on_status(status): tick 이 성공할 때마다 같은 스레드에서 부른다 (LED 연동 등).
        - tick·on_status 가 예상 못 한 예외를 내면 로그만 남기고 계속 돈다.
        - wait(timeout) -> 멈춰야 하면 True. 시험용으로 바꿔 끼울 수 있고 기본은 Event.wait.
        """
        wait = wait or self._stop.wait
        interval = self.cfg["interval_s"]
        next_t = self._clock()
        while not self._stop.is_set():
            try:
                status = self.tick()
            except Exception:
                log.exception("tick 실패. 다음 주기에 다시 함")
                status = None
            if status is not None and on_status is not None:
                try:
                    on_status(status)
                except Exception:
                    log.exception("on_status 실패")
            next_t += interval
            now = self._clock()
            if now >= next_t:
                missed = int((now - next_t) // interval) + 1
                log.warning("tick 이 늦어 %d 주기를 건너뜀", missed)
                next_t += missed * interval
            if wait(next_t - now):
                break
