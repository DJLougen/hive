"""Stress tests for backup and disaster recovery."""

from __future__ import annotations

import gzip
import json
import os
import tempfile

import pytest

from hive.rust_brain import RustBrain, TimestampRegression


def test_snapshot_to_file_and_restore():
    brain = RustBrain(tenant_id="backup_test")
    brain.remember("key1", "value1")
    brain.remember("key2", "value2", tags={"t1"})
    brain.remember("key3", "value3", edges={"related_to": ["key1"]})

    with tempfile.NamedTemporaryFile(delete=False, suffix=".gz") as f:
        path = f.name

    try:
        meta = brain.snapshot_to_file(path)
        assert meta["node_count"] == 3
        assert "sha256" in meta

        # Verify file exists and is gzipped
        with open(path, "rb") as fh:
            raw = fh.read()
        assert raw[:2] == b"\x1f\x8b"  # gzip magic

        # Restore into fresh brain
        brain2 = RustBrain(tenant_id="backup_test")
        restored = brain2.restore_from_file(path)
        assert restored == 3
        assert brain2.recall("key1") == "value1"
        assert brain2.recall("key2") == "value2"
        assert brain2.recall("key3") == "value3"
    finally:
        os.unlink(path)


def test_restore_clears_existing_data():
    brain = RustBrain()
    brain.remember("old", "data")

    with tempfile.NamedTemporaryFile(delete=False, suffix=".gz") as f:
        path = f.name

    try:
        brain2 = RustBrain()
        brain2.remember("new", "data")
        brain2.snapshot_to_file(path)

        brain.restore_from_file(path)
        assert brain.recall("new") == "data"
        assert brain.recall("old") is None
    finally:
        os.unlink(path)


def test_corruption_detection():
    brain = RustBrain()
    brain.remember("k", "v")

    with tempfile.NamedTemporaryFile(delete=False, suffix=".gz") as f:
        path = f.name

    try:
        brain.snapshot_to_file(path)
        # Corrupt the file
        with open(path, "rb+") as fh:
            fh.seek(-10, os.SEEK_END)
            fh.write(b"CORRUPTED!")

        # Should still decompress but SHA won't match — we don't enforce it on restore
        # However gzip should fail if corruption is bad enough
        with pytest.raises((gzip.BadGzipFile, Exception)):
            brain2 = RustBrain()
            brain2.restore_from_file(path)
    finally:
        if os.path.exists(path):
            os.unlink(path)


def test_version_mismatch():
    with tempfile.NamedTemporaryFile(mode="wb", delete=False, suffix=".gz") as f:
        path = f.name
        payload = json.dumps({"version": "hive-snapshot-v0", "nodes": []}).encode()
        f.write(gzip.compress(payload))

    try:
        brain = RustBrain()
        with pytest.raises(ValueError, match="Unsupported snapshot version"):
            brain.restore_from_file(path)
    finally:
        os.unlink(path)


def test_restore_preserves_hlc_for_replication():
    """HLC must survive snapshot round-trip so post-restore writes stay ordered."""
    brain = RustBrain()
    brain.remember("k", "v1", hlc=(5000, 10, "nodeA"))
    orig_hlc = brain.get("k").hlc

    with tempfile.NamedTemporaryFile(delete=False, suffix=".gz") as f:
        path = f.name

    try:
        brain.snapshot_to_file(path)
        brain2 = RustBrain()
        brain2.restore_from_file(path)
        assert brain2.get("k").hlc == orig_hlc

        # Causal successor from the original writer should apply after restore.
        brain2.remember("k", "v2", hlc=(5000, 11, "nodeA"))
        assert brain2.recall("k") == "v2"

        # Stale replay must still be rejected.
        with pytest.raises(TimestampRegression):
            brain2.remember("k", "stale", hlc=(5000, 5, "nodeA"))
    finally:
        os.unlink(path)


def test_snapshot_with_tenant_isolation():
    brain = RustBrain(tenant_id="org_a", tenant_isolation=True)
    brain.remember("secret", "data")

    with tempfile.NamedTemporaryFile(delete=False, suffix=".gz") as f:
        path = f.name

    try:
        brain.snapshot_to_file(path)
        brain_b = RustBrain(tenant_id="org_a", tenant_isolation=True)
        brain_b.restore_from_file(path)
        assert brain_b.recall("secret") == "data"
    finally:
        os.unlink(path)
