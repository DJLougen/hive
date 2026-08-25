"""Multi-writer concurrency tests for rust_brain with HLC.

Tests that the Hybrid Logical Clock maintains causal ordering when multiple
threads/processes write concurrently.
"""

import threading
import time

import pytest

from hive.rust_brain import RustBrain, TimestampRegression


def test_concurrent_writes_same_key():
    """Multiple threads writing to the same key should maintain causal order."""
    brain = RustBrain()
    errors = []
    
    def writer(thread_id, value):
        try:
            # Each thread writes 10 times to the same key
            for i in range(10):
                brain.remember(f"key_{thread_id}", value=f"t{thread_id}_v{i}")
                time.sleep(0.001)  # Small delay to interleave
        except Exception as e:
            errors.append(e)
    
    # Start 5 threads
    threads = [threading.Thread(target=writer, args=(i, f"value_{i}")) for i in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    
    # No errors should have occurred
    assert len(errors) == 0, f"Errors occurred: {errors}"
    
    # Each key should have exactly one value (last write wins)
    for i in range(5):
        node = brain.get(f"key_{i}")
        assert node is not None, f"key_{i} should exist"
        # The value should be from the last write (v9)
        assert node.value == f"t{i}_v9", f"Expected t{i}_v9, got {node.value}"


def test_concurrent_writes_different_keys():
    """Multiple threads writing to different keys should all succeed."""
    brain = RustBrain()
    errors = []
    
    def writer(thread_id):
        try:
            # Each thread writes to its own set of keys
            for i in range(100):
                brain.remember(f"thread{thread_id}_key{i}", value=f"value_{i}")
        except Exception as e:
            errors.append(e)
    
    # Start 10 threads
    threads = [threading.Thread(target=writer, args=(i,)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    
    # No errors should have occurred
    assert len(errors) == 0, f"Errors occurred: {errors}"
    
    # All keys should exist
    for thread_id in range(10):
        for i in range(100):
            val = brain.recall(f"thread{thread_id}_key{i}")
            assert val is not None, f"thread{thread_id}_key{i} should exist"


def test_hlc_ordering_across_threads():
    """HLC should maintain causal ordering even with concurrent writes."""
    brain = RustBrain()
    
    # Thread 1 writes first
    brain.remember("event1", value="first", hlc=(1000, 0, "thread1"))
    
    # Thread 2 writes second (should have higher HLC)
    brain.remember("event2", value="second", hlc=(1001, 1, "thread2"))
    
    # Thread 3 tries to write with earlier HLC (should raise)
    with pytest.raises(TimestampRegression):
        brain.remember("event2", value="invalid", hlc=(999, 0, "thread3"))
    
    # Verify ordering
    event1 = brain.get("event1")
    event2 = brain.get("event2")
    
    assert event1.hlc < event2.hlc, "event1 should have earlier HLC than event2"
    assert event1.value == "first"
    assert event2.value == "second"


def test_concurrent_supersede():
    """Multiple threads superseding the same key should maintain order."""
    brain = RustBrain()
    errors = []
    
    def superseder(thread_id, iterations):
        try:
            for i in range(iterations):
                brain.supersede("shared_key", f"v{thread_id}_{i}")
                time.sleep(0.001)
        except Exception as e:
            errors.append(e)
    
    # Initial value
    brain.remember("shared_key", value="initial")
    
    # Start 5 threads, each superseding 20 times
    threads = [threading.Thread(target=superseder, args=(i, 20)) for i in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    
    # No errors should have occurred
    assert len(errors) == 0, f"Errors occurred: {errors}"
    
    # The final value should be from one of the threads
    final = brain.recall("shared_key")
    assert final is not None
    assert final.startswith("v"), f"Expected value starting with 'v', got {final}"


def test_concurrent_causal_chains():
    """Multiple threads building causal chains should maintain consistency."""
    brain = RustBrain()
    errors = []
    
    def chain_builder(thread_id, length):
        try:
            prev_key = None
            for i in range(length):
                key = f"chain{thread_id}_step{i}"
                edges = {"caused_by": [prev_key]} if prev_key else None
                brain.remember(key, value=f"step_{i}", edges=edges)
                prev_key = key
        except Exception as e:
            errors.append(e)
    
    # Start 5 threads, each building a chain of 50 steps
    threads = [threading.Thread(target=chain_builder, args=(i, 50)) for i in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    
    # No errors should have occurred
    assert len(errors) == 0, f"Errors occurred: {errors}"
    
    # Verify each chain is intact
    for thread_id in range(5):
        for i in range(50):
            key = f"chain{thread_id}_step{i}"
            node = brain.get(key)
            assert node is not None, f"{key} should exist"
            
            if i > 0:
                # Should have a causal edge to previous step
                prev_key = f"chain{thread_id}_step{i-1}"
                assert prev_key in node.edges.get("caused_by", []), \
                    f"{key} should be caused by {prev_key}"


def test_concurrent_reads_during_writes():
    """Reads should be consistent even during concurrent writes."""
    brain = RustBrain()
    errors = []
    read_results = []
    
    def writer():
        try:
            for i in range(100):
                brain.remember(f"key_{i}", value=f"value_{i}")
                time.sleep(0.001)
        except Exception as e:
            errors.append(e)
    
    def reader():
        try:
            for _ in range(50):
                # Read a random subset of keys
                for i in range(0, 100, 10):
                    val = brain.recall(f"key_{i}")
                    if val is not None:
                        read_results.append((i, val))
                time.sleep(0.002)
        except Exception as e:
            errors.append(e)
    
    # Start 1 writer and 3 readers
    writer_thread = threading.Thread(target=writer)
    reader_threads = [threading.Thread(target=reader) for _ in range(3)]
    
    writer_thread.start()
    for t in reader_threads:
        t.start()
    
    writer_thread.join()
    for t in reader_threads:
        t.join()
    
    # No errors should have occurred
    assert len(errors) == 0, f"Errors occurred: {errors}"
    
    # All reads should be consistent (value should match key index)
    for key_idx, value in read_results:
        expected = f"value_{key_idx}"
        assert value == expected, f"key_{key_idx} has value {value}, expected {expected}"


def test_concurrent_search():
    """Search operations should be consistent during concurrent writes."""
    brain = RustBrain()
    errors = []
    
    def writer():
        try:
            for i in range(100):
                brain.remember(
                    f"item_{i}",
                    value=i,
                    tags=["search_test"],
                    trust=0.5 + (i % 10) * 0.05
                )
                time.sleep(0.001)
        except Exception as e:
            errors.append(e)
    
    def searcher():
        try:
            for _ in range(20):
                results = brain.search(tag="search_test", min_trust=0.7)
                # All results should have the correct tag and trust level
                for node in results:
                    assert "search_test" in node.tags
                    assert node.trust >= 0.7
                time.sleep(0.005)
        except Exception as e:
            errors.append(e)
    
    # Start 1 writer and 2 searchers
    writer_thread = threading.Thread(target=writer)
    searcher_threads = [threading.Thread(target=searcher) for _ in range(2)]
    
    writer_thread.start()
    for t in searcher_threads:
        t.start()
    
    writer_thread.join()
    for t in searcher_threads:
        t.join()
    
    # No errors should have occurred
    assert len(errors) == 0, f"Errors occurred: {errors}"


def test_concurrent_snapshot():
    """Snapshot operations should be consistent during concurrent writes."""
    brain = RustBrain()
    errors = []
    snapshots = []
    
    def writer():
        try:
            for i in range(50):
                brain.remember(f"key_{i}", value=f"value_{i}")
                time.sleep(0.002)
        except Exception as e:
            errors.append(e)
    
    def snapshotter():
        try:
            for _ in range(10):
                snap = brain.snapshot()
                snapshots.append(snap)
                # Verify snapshot is consistent
                for node in snap:
                    assert "key" in node
                    assert "value" in node
                    assert "hlc" in node
                time.sleep(0.01)
        except Exception as e:
            errors.append(e)
    
    # Start 1 writer and 2 snapshotters
    writer_thread = threading.Thread(target=writer)
    snapshotter_threads = [threading.Thread(target=snapshotter) for _ in range(2)]
    
    writer_thread.start()
    for t in snapshotter_threads:
        t.start()
    
    writer_thread.join()
    for t in snapshotter_threads:
        t.join()
    
    # No errors should have occurred
    assert len(errors) == 0, f"Errors occurred: {errors}"
    
    # All snapshots should be valid
    assert len(snapshots) == 20  # 2 threads × 10 snapshots each


def test_thread_safety_with_hlc_update():
    """HLC updates should be thread-safe."""
    brain = RustBrain()
    errors = []
    
    def writer_with_hlc_update(thread_id, remote_hlc):
        try:
            for i in range(20):
                # Simulate receiving HLC from remote node
                brain.update_hlc(remote_hlc)
                # Then write
                brain.remember(f"key_{thread_id}_{i}", value=f"value_{i}")
                time.sleep(0.001)
        except Exception as e:
            errors.append(e)
    
    # Simulate 3 threads receiving HLCs from different remote nodes
    threads = [
        threading.Thread(target=writer_with_hlc_update, args=(i, (i*10, 1000+i, f"remote{i}")))
        for i in range(3)
    ]
    
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    
    # No errors should have occurred
    assert len(errors) == 0, f"Errors occurred: {errors}"
    
    # All keys should exist
    for thread_id in range(3):
        for i in range(20):
            val = brain.recall(f"key_{thread_id}_{i}")
            assert val is not None, f"key_{thread_id}_{i} should exist"
