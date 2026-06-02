"""Stress tests for encryption at rest."""

from __future__ import annotations

import os

import pytest

from hive.encryption import Encryptor


def test_encrypt_decrypt_roundtrip():
    os.environ["HIVE_ENCRYPTION_KEY"] = "test-passphrase-for-unit-tests"
    try:
        e = Encryptor.from_env()
        assert e.is_enabled()
        ciphertext = e.encrypt("sensitive-data")
        assert ciphertext != "sensitive-data"
        assert isinstance(ciphertext, str)
        decrypted = e.decrypt(ciphertext)
        assert decrypted == "sensitive-data"
    finally:
        del os.environ["HIVE_ENCRYPTION_KEY"]


def test_encrypt_dict_value():
    os.environ["HIVE_ENCRYPTION_KEY"] = "test-passphrase"
    try:
        e = Encryptor.from_env()
        data = {"api_key": "sk-123", "tenant": "org-a"}
        ct = e.encrypt(data)
        assert e.decrypt(ct) == data
    finally:
        del os.environ["HIVE_ENCRYPTION_KEY"]


def test_disabled_mode_no_env():
    for key in list(os.environ):
        if key == "HIVE_ENCRYPTION_KEY":
            del os.environ[key]
    e = Encryptor.from_env()
    assert not e.is_enabled()
    ct = e.encrypt("plain")
    assert e.decrypt(ct) == "plain"


def test_tamper_detection():
    os.environ["HIVE_ENCRYPTION_KEY"] = "test-passphrase"
    try:
        e = Encryptor.from_env()
        ct = e.encrypt("secret")
        # Corrupt the ciphertext
        corrupted = ct[:-4] + "XXXX"
        with pytest.raises(Exception):
            e.decrypt(corrupted)
    finally:
        del os.environ["HIVE_ENCRYPTION_KEY"]


def test_key_derivation_is_deterministic():
    os.environ["HIVE_ENCRYPTION_KEY"] = "same-passphrase"
    try:
        e1 = Encryptor.from_env()
        e2 = Encryptor.from_env()
        ct = e1.encrypt("data")
        assert e2.decrypt(ct) == "data"
    finally:
        del os.environ["HIVE_ENCRYPTION_KEY"]


def test_different_keys_produce_different_ciphertexts():
    os.environ["HIVE_ENCRYPTION_KEY"] = "key-a"
    try:
        e1 = Encryptor.from_env()
    finally:
        del os.environ["HIVE_ENCRYPTION_KEY"]

    os.environ["HIVE_ENCRYPTION_KEY"] = "key-b"
    try:
        e2 = Encryptor.from_env()
    finally:
        del os.environ["HIVE_ENCRYPTION_KEY"]

    ct1 = e1.encrypt("data")
    ct2 = e2.encrypt("data")
    assert ct1 != ct2
    # e2 should NOT decrypt e1's ciphertext
    with pytest.raises(Exception):
        e2.decrypt(ct1)
