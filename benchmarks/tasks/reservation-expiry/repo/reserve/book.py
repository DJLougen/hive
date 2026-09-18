"""Holding a slot for a TTL, then confirming it."""

from __future__ import annotations


class SlotTaken(Exception):
    """Raised when a slot is already held or booked."""


class HoldExpired(Exception):
    """Raised when a confirm arrives after the hold's deadline."""


class Reservations:
    """Reserve a slot for ``ttl`` seconds, then confirm or lose it.

    A hold is recorded with the time it was taken; nothing yet checks that
    time, so a hold lives forever.
    """

    def __init__(self, ttl: float) -> None:
        if ttl <= 0:
            raise ValueError("ttl must be positive")
        self.ttl = ttl
        self._holds: dict[str, float] = {}      # slot -> reserved_at
        self._confirmed: set[str] = set()

    def reserve(self, slot: str, now: float) -> float:
        """Hold ``slot`` at ``now``; return the expiry time."""
        if slot in self._confirmed or slot in self._holds:
            raise SlotTaken(slot)
        self._holds[slot] = now
        return now + self.ttl

    def confirm(self, slot: str, now: float) -> None:
        """Finalize a held slot. Raises HoldExpired after the deadline."""
        if slot in self._confirmed:
            return
        if slot not in self._holds:
            raise HoldExpired(f"{slot}: no live hold")
        del self._holds[slot]
        self._confirmed.add(slot)

    def is_free(self, slot: str, now: float) -> bool:
        """Is ``slot`` available to reserve at ``now``?"""
        return slot not in self._confirmed and slot not in self._holds
