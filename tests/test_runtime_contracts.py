"""Runtime contracts: HLC monotonicity and injected-memory preservation.

Clock tests run on a per-test ``HybridLogicalClock`` plus a fake ``time``
module monkeypatched into ``hive.rust_brain``, so deterministic rollback
scenarios never contaminate the shared process clock or other tests.
"""

from __future__ import annotations

import threading

import pytest

from hive import rust_brain as rb
from hive.rust_brain import HermesBackend, HybridLogicalClock, RustBrain


class _FakeTime:
    """Deterministic stand-in for the ``time`` module inside rust_brain."""

    def __init__(self, t: int = 1_000_000):
        self.t = t

    def time_ns(self) -> int:
        return self.t


@pytest.fixture
def fake_clock(monkeypatch):
    """Isolate the global HLC and wall clock for one test.

    ``MemoryNode``'s default factory and ``RustBrain.remember`` resolve the
    module-global ``_hlc`` at call time, so swapping it here keeps every
    write in the test on the fresh clock and leaves the process clock
    untouched for other tests.
    """
    fake = _FakeTime()
    clock = HybridLogicalClock(node_id="test-node")
    monkeypatch.setattr(rb, "time", fake)
    monkeypatch.setattr(rb, "_hlc", clock)
    return fake, clock


# ---------------------------------------------------------------------------
# HLC.now: the wall component must never regress
# ---------------------------------------------------------------------------

def test_hlc_now_retains_wall_on_rollback(fake_clock):
    fake, clock = fake_clock
    first = clock.now()
    fake.t -= 1_000  # NTP correction / clock rollback
    second = clock.now()
    assert second[0] == first[0]  # prior wall retained, not the regressed read
    assert second > first         # logical counter carries the tick


def test_hlc_now_stays_monotonic_under_repeated_rollback(fake_clock):
    fake, clock = fake_clock
    stamps = [clock.now()]
    for _ in range(5):
        fake.t -= 500
        stamps.append(clock.now())
    assert stamps == sorted(stamps)
    assert len(set(stamps)) == len(stamps)


# ---------------------------------------------------------------------------
# HLC.update: three-way max over local prior / physical / received
# ---------------------------------------------------------------------------

def test_hlc_update_adopts_dominant_received_wall(fake_clock):
    _, clock = fake_clock
    clock.now()  # last_wall = 1_000_000
    clock.update((5_000_000, 3, "peer"))
    nxt = clock.now()
    assert nxt[0] == 5_000_000
    assert nxt > (5_000_000, 3, "peer")


def test_hlc_update_never_regresses_local_wall(fake_clock):
    _, clock = fake_clock
    clock.update((5_000_000, 3, "peer"))   # last_wall = 5_000_000
    prev = clock.now()
    clock.update((4_000_000, 9, "peer"))   # stale message behind our wall
    nxt = clock.now()
    assert nxt[0] == 5_000_000             # local prior wall wins the max
    assert nxt > prev
    assert nxt > (4_000_000, 9, "peer")


def test_hlc_update_physical_wall_resets_logical(fake_clock):
    fake, clock = fake_clock
    clock.update((5_000_000, 3, "peer"))
    clock.now()
    fake.t = 9_000_000                     # physical clock jumps past both
    clock.update((100, 0, "peer"))
    nxt = clock.now()
    assert nxt[0] == 9_000_000


def test_hlc_update_equal_walls_stays_ahead_of_both_logicals(fake_clock):
    _, clock = fake_clock
    clock.update((5_000_000, 3, "peer"))
    clock.now()                            # last_wall = 5_000_000
    clock.update((5_000_000, 7, "peer"))   # same wall, higher logical
    nxt = clock.now()
    assert nxt[0] == 5_000_000
    assert nxt > (5_000_000, 7, "peer")


# ---------------------------------------------------------------------------
# Write-path regressions driven by the clock
# ---------------------------------------------------------------------------

def test_same_key_writes_across_wall_rollback(fake_clock):
    """A wall-clock rollback must not turn a normal rewrite into a
    TimestampRegression: the retained wall keeps the second write ahead."""
    fake, _ = fake_clock
    brain = RustBrain()
    n1 = brain.remember("k", "v1")
    fake.t -= 1_000
    n2 = brain.remember("k", "v2")
    assert n2.hlc > n1.hlc
    assert brain.recall("k") == "v2"


def test_restored_future_hlc_yields_newer_local_writes(fake_clock, tmp_path):
    """After restoring a snapshot whose HLCs are ahead of the local wall
    clock, subsequent local writes must still be causally newer — even when
    a stale gossip update arrives in between."""
    _fake, _ = fake_clock
    source = RustBrain()
    source.remember("k", "v1", hlc=(9_999_999_999, 42, "future-node"))
    path = tmp_path / "snap.gz"
    source.snapshot_to_file(str(path))

    restored = RustBrain()
    restored.restore_from_file(str(path))
    # A stale peer event must not drag the clock back under the restored wall.
    restored.update_hlc((5_000, 0, "stale-peer"))
    node = restored.remember("k", "v2")
    assert node.hlc > (9_999_999_999, 42, "future-node")
    assert restored.recall("k") == "v2"


# ---------------------------------------------------------------------------
# Threaded clock behaviour under deterministic time
# ---------------------------------------------------------------------------

def test_hlc_now_unique_across_threads(fake_clock):
    """Concurrent now() calls on a frozen wall clock must still produce
    unique, per-thread monotonically increasing timestamps."""
    fake, clock = fake_clock
    fake.t = 42_000_000  # frozen: uniqueness comes from the logical counter
    barrier = threading.Barrier(8)
    results: list[list[tuple[int, int, str]]] = [[] for _ in range(8)]

    def worker(i: int) -> None:
        barrier.wait()
        for _ in range(50):
            results[i].append(clock.now())

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    all_ts = [ts for per_thread in results for ts in per_thread]
    assert len(set(all_ts)) == len(all_ts) == 8 * 50
    for per_thread in results:
        assert per_thread == sorted(per_thread)


def test_hlc_update_causal_monotonicity_across_threads(fake_clock):
    """now() after update(received) must always land strictly ahead of the
    received timestamp, no matter how updates interleave across threads."""
    fake, clock = fake_clock
    barrier = threading.Barrier(4)
    pairs: list[tuple[tuple[int, int, str], tuple[int, int, str]]] = []
    lock = threading.Lock()

    def worker(i: int) -> None:
        barrier.wait()
        for j in range(25):
            received = (fake.t + i * 1_000 + j, j, f"peer{i}")
            clock.update(received)
            local = clock.now()
            with lock:
                pairs.append((received, local))

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(pairs) == 4 * 25
    for received, local in pairs:
        assert local > received


# ---------------------------------------------------------------------------
# Injected memory must be preserved, not replaced by a falsy empty store
# ---------------------------------------------------------------------------

def test_stack_preserves_injected_empty_brain():
    from hive import HiveStack
    from hive.rule_fast import RuleFastHoneyComb

    brain = RustBrain()
    assert not brain  # empty RustBrain is falsy via __len__ — the trap
    stack = HiveStack(honey_comb=RuleFastHoneyComb(), rust_brain=brain)
    assert stack.brain is brain


def test_hermes_backend_preserves_injected_empty_brain():
    brain = RustBrain()
    backend = HermesBackend(brain=brain)
    assert backend.brain is brain
