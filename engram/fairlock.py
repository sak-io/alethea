"""FIFO-fair re-entrant lock (ADR 0005).

An ordinary RLock lets a thread that releases and immediately re-acquires (the sleep
cycle, per batch) win again and again while waiters starve. Here a releasing thread
that re-acquires queues behind everyone already waiting.
"""
import threading
from collections import deque


class FairLock:
    def __init__(self):
        self._cond = threading.Condition(threading.Lock())
        self._owner = None
        self._count = 0
        self._queue = deque()

    def acquire(self, blocking=True, timeout=-1):
        me = threading.get_ident()
        with self._cond:
            if self._owner == me:
                self._count += 1
                return True
            if not blocking and (self._owner is not None or self._queue):
                return False
            ticket = object()
            self._queue.append(ticket)
            try:
                ok = self._cond.wait_for(
                    lambda: self._owner is None and self._queue[0] is ticket,
                    None if timeout is None or timeout < 0 else timeout)
            except BaseException:
                self._queue.remove(ticket)
                self._cond.notify_all()
                raise
            if not ok:
                self._queue.remove(ticket)
                self._cond.notify_all()
                return False
            self._queue.popleft()
            self._owner, self._count = me, 1
            return True

    def release(self):
        with self._cond:
            if self._owner != threading.get_ident():
                raise RuntimeError("cannot release an un-acquired lock")
            self._count -= 1
            if self._count == 0:
                self._owner = None
                self._cond.notify_all()

    __enter__ = acquire

    def __exit__(self, *exc):
        self.release()
        return False
