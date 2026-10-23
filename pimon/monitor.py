"""Monitor: tick() = 수집 → 판정 → 현재 상태 저장.

현재 상태는 잠금 아래에 두고, status() 는 복사본을 돌려준다 (web 스레드가 읽어도 안전).
run()·stop() 주기 반복은 7단계에서 붙인다.
"""

import copy
import os
import threading
from datetime import datetime

from pimon.collect import collect_all
from pimon.judge import judge_all


def _now():
    return datetime.now().astimezone()


class Monitor:
    def __init__(self, root, cfg, statvfs=os.statvfs, now=_now):
        self.root = root
        self.cfg = cfg
        self._statvfs = statvfs
        self._now = now
        self._lock = threading.Lock()
        self._status = None

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
