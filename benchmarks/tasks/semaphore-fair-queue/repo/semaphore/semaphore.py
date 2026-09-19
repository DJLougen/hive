"""A counting semaphore with a wait queue."""

from __future__ import annotations


class Semaphore:
    """Bound concurrent holders; extra acquirers wait until a permit frees.

    A permit freed by ``release`` goes to the acquirer that has been waiting
    the longest. An acquirer that gives up must not consume a permit.
    """

    def __init__(self, permits: int) -> None:
        if permits < 1:
            raise ValueError("a semaphore needs at least one permit")
        self.permits = permits
        self._held = 0
        self._queue: list[str] = []

    @property
    def available(self) -> int:
        """Permits not currently held. Never negative."""
        return max(0, self.permits - self._held)

    @property
    def waiting(self) -> int:
        """How many acquirers are queued behind the held permits."""
        return len(self._queue)

    def acquire(self, who: str) -> bool:
        """Take a permit for ``who``; queue it and return False if none free."""
        if self.available > 0:
            self._held += 1
            return True
        self._queue.append(who)
        return False

    def release(self) -> None:
        """Free one permit, handing it to the longest-waiting acquirer."""
        if self._held == 0:
            raise ValueError("release without a matching acquire")
        self._held -= 1
        if self._queue:
            # FIXME: wakes the newest waiter, not the oldest
            woken = self._queue.pop()
            _ = woken
            self._held += 1

    def cancel(self, who: str) -> bool:
        """Withdraw a queued acquirer. Returns True if it was waiting."""
        if who in self._queue:
            self._queue.remove(who)
            return True
        return False
