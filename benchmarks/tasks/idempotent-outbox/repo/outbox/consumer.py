"""Applying sequenced account-update messages exactly once."""

from __future__ import annotations


class Consumer:
    """Apply (account, seq, delta) messages to per-account balances.

    Every message is applied as it arrives: redelivery double-counts and an
    out-of-order older message rewinds the balance.
    """

    def __init__(self) -> None:
        self.balances: dict[str, int] = {}

    def apply(self, account: str, seq: int, delta: int) -> bool:
        """Apply ``delta`` to ``account``'s balance. Returns True if applied."""
        self.balances[account] = self.balances.get(account, 0) + delta
        return True

    def balance(self, account: str) -> int:
        return self.balances.get(account, 0)
