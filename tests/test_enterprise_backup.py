"""Stress tests for backup and disaster recovery."""

from __future__ import annotations

import gzip
import json
import os
import tempfile

import pytest

from hive.rust_brain import RustBrain


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
    """Tampering with snapshot content is caught by the embedded SHA-256."""
    brain = RustBrain()
    brain.remember("k", "v")

    with tempfile.NamedTemporaryFile(delete=False, suffix=".gz") as f:
        path = f.name

    try:
        brain.snapshot_to_file(path)
        # Tamper with a node value while keeping the gzip+JSON framing intact,
        # so only the checksum can catch it.
        with open(path, "rb") as fh:
            data = json.loads(gzip.decompress(fh.read()).decode("utf-8"))
        data["nodes"][0]["value"] = "tampered"
        with open(path, "wb") as fh:
            fh.write(gzip.compress(json.dumps(data).encode("utf-8")))

        brain2 = RustBrain()
        with pytest.raises(ValueError, match="checksum mismatch"):
            brain2.restore_from_file(path)
    finally:
        if os.path.exists(path):
            os.unlink(path)


def test_corruption_detection_leaves_existing_data_intact():
    """A failed restore must not wipe the destination store."""
    brain = RustBrain()
    brain.remember("k", "v")

    with tempfile.NamedTemporaryFile(delete=False, suffix=".gz") as f:
        path = f.name

    try:
        brain.snapshot_to_file(path)
        with open(path, "rb") as fh:
            data = json.loads(gzip.decompress(fh.read()).decode("utf-8"))
        data["nodes"][0]["value"] = "tampered"
        with open(path, "wb") as fh:
            fh.write(gzip.compress(json.dumps(data).encode("utf-8")))

        target = RustBrain()
        target.remember("keep", "safe")
        with pytest.raises(ValueError, match="checksum mismatch"):
            target.restore_from_file(path)
        # Pre-existing data survives the aborted restore.
        assert target.recall("keep") == "safe"
    finally:
        if os.path.exists(path):
            os.unlink(path)


def test_truncated_snapshot_raises():
    """Raw byte corruption that breaks gzip/JSON framing also fails loudly."""
    brain = RustBrain()
    brain.remember("k", "v")

    with tempfile.NamedTemporaryFile(delete=False, suffix=".gz") as f:
        path = f.name

    try:
        brain.snapshot_to_file(path)
        with open(path, "rb+") as fh:
            fh.seek(-10, os.SEEK_END)
            fh.write(b"CORRUPTED!")
        brain2 = RustBrain()
        with pytest.raises(Exception):
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
