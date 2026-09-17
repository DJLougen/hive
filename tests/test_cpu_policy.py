"""Tests for hive.cpu_policy — the trainable CPU routing policy."""

from __future__ import annotations

import pytest

from hive.cpu_policy import CPURouterPolicy, featurize, resolve_args, trajectory_row


def _rows() -> list[dict]:
    """A tiny imitation dataset: the canonical mechanical workflow."""
    base = {"listed": False, "tests_run": 0, "tests_passed": None, "writes": 0,
            "files_read": [], "suggested_read": None, "verify_pending": None,
            "recalled_fix": None, "memory_hit": False, "fail_signature": None,
            "step": 0, "last_tool": None}
    rows = []
    for i in range(8):
        s = dict(base, step=i * 10)
        rows.append({"state": s, "tool": "list_files", "ok": True})
        s2 = dict(s, listed=True, last_tool="list_files")
        rows.append({"state": s2, "tool": "run_tests", "ok": True})
        s3 = dict(s2, tests_run=1, tests_passed=False, suggested_read="tests/test_a.py",
                  fail_signature="E assert 1 == 2", last_tool="run_tests")
        rows.append({"state": s3, "tool": "read_file", "ok": True})
        s4 = dict(s3, files_read=["tests/test_a.py", "a.py"], suggested_read=None,
                  last_tool="read_file")
        rows.append({"state": s4, "tool": "finish", "ok": True})
    return rows


def _fit() -> CPURouterPolicy:
    p = CPURouterPolicy(threshold=0.3)
    p.fit(_rows())
    return p


def test_featurize_fixed_width_and_stable():
    v = featurize({"listed": True, "tests_run": 2, "tests_passed": False,
                   "files_read": ["a.py"], "last_tool": "read_file"})
    assert len(v) == len(featurize({}))
    assert v[0] == 1.0 and v[1] == 2.0 and v[2] == 0.0


def test_unfitted_policy_escalates():
    d = CPURouterPolicy().predict({"listed": False})
    assert d["escalated"] and d["tool"] == "escalate"


def test_fitted_routes_mechanical_steps():
    p = _fit()
    d = p.predict({"listed": False, "tests_run": 0, "tests_passed": None,
                   "writes": 0, "files_read": [], "step": 0, "last_tool": None})
    assert d["tool"] == "list_files" and not d["escalated"]

    d = p.predict({"listed": True, "tests_run": 1, "tests_passed": False,
                   "writes": 0, "files_read": [], "suggested_read": "tests/test_a.py",
                   "step": 2, "last_tool": "run_tests"})
    assert d["tool"] == "read_file" and d["args"]["path"] == "tests/test_a.py"


def test_write_file_without_recall_escalates():
    p = _fit()
    d = p.predict({"listed": True, "tests_run": 1, "tests_passed": False,
                   "writes": 0, "files_read": ["a.py"], "suggested_read": None,
                   "step": 3, "last_tool": "read_file"})
    # Whatever the classifier guesses, a write without a recalled fix can
    # never be routed — patch synthesis needs the LLM.
    assert not (d["tool"] == "write_file" and not d["escalated"])


def test_recalled_fix_replays_mechanically():
    p = _fit()
    fix = {"path": "a.py", "content": "x = 1\n"}
    d = p.predict({"listed": False, "tests_run": 0, "tests_passed": None,
                   "writes": 0, "files_read": [], "recalled_fix": fix,
                   "step": 0, "last_tool": None})
    assert d["tool"] == "write_file" and not d["escalated"]
    assert d["args"] == fix


def test_loop_guard_escalates_identical_route():
    p = _fit()
    s = {"listed": False, "tests_run": 0, "tests_passed": None, "writes": 0,
         "files_read": [], "step": 0, "last_tool": None}
    first = p.predict(s)
    assert first["tool"] == "list_files"
    second = p.predict(s)  # state did not advance -> must not re-route
    assert second["escalated"]
    assert "loop guard" in second["args"]["reason"]


def test_resolve_args_never_rereads():
    s = {"suggested_read": "a.py", "files_read": ["a.py"]}
    assert resolve_args("read_file", s) is None


def test_save_load_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("HIVE_ALLOW_UNSIGNED_MODEL", "1")
    p = _fit()
    out = tmp_path / "m.joblib"
    p.save(out)
    q = CPURouterPolicy.load(out)
    s = {"listed": False, "tests_run": 0, "tests_passed": None, "writes": 0,
         "files_read": [], "step": 0, "last_tool": None}
    assert q.predict(s)["tool"] == "list_files"


def test_fit_rejects_empty():
    with pytest.raises(ValueError):
        CPURouterPolicy().fit([])


def test_fit_skips_failed_and_unknown_rows():
    rows = _rows()
    rows.append({"state": {"listed": False}, "tool": "list_files", "ok": False})
    rows.append({"state": {"listed": True}, "tool": "not_a_tool", "ok": True})
    m = CPURouterPolicy().fit(rows)
    assert m["samples"] == len(_rows())  # bad/unknown rows dropped


def test_markov_algorithm_routes_and_replays():
    p = CPURouterPolicy(threshold=0.3, algorithm="markov2")
    p.fit(_rows())
    d = p.predict({"listed": False, "tests_run": 0, "tests_passed": None,
                   "writes": 0, "files_read": [], "step": 0, "last_tool": None})
    assert d["tool"] == "list_files" and not d["escalated"]
    # markov learns the transition table — no featurize needed
    d = p.predict({"last_tool": "run_tests", "prev2_tool": "list_files",
                   "suggested_read": "tests/test_a.py"})
    assert d["tool"] == "read_file"


def test_algorithm_validation_and_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("HIVE_ALLOW_UNSIGNED_MODEL", "1")
    with pytest.raises(ValueError):
        CPURouterPolicy(algorithm="quantum")
    p = CPURouterPolicy(threshold=0.3, algorithm="markov1")
    p.fit(_rows())
    out = tmp_path / "m.joblib"
    p.save(out)
    q = CPURouterPolicy.load(out)
    assert q.algorithm == "markov1"
    assert q.predict({"last_tool": "list_files"})["tool"] == "run_tests"


def test_trajectory_row_shape():
    row = trajectory_row({"listed": True, "files_read": ["a"], "extra": "dropped"},
                         "list_files", ok=True)
    assert row["tool"] == "list_files" and row["ok"] is True
    assert "extra" not in row["state"]
    assert row["state"]["files_read"] == ["a"]


def test_load_refuses_unsigned_model(tmp_path, monkeypatch):
    from hive.model_registry import UnsignedModelError

    monkeypatch.delenv("HIVE_ALLOW_UNSIGNED_MODEL", raising=False)
    p = _fit()
    out = tmp_path / "m.joblib"
    p.save(out)
    with pytest.raises(UnsignedModelError, match="HIVE_ALLOW_UNSIGNED_MODEL"):
        CPURouterPolicy.load(out)


def test_load_unsigned_model_with_env_opt_in(tmp_path, monkeypatch):
    monkeypatch.setenv("HIVE_ALLOW_UNSIGNED_MODEL", "1")
    p = _fit()
    out = tmp_path / "m.joblib"
    p.save(out)
    q = CPURouterPolicy.load(out)
    assert q.predict({"listed": False})["tool"] == "list_files"


def test_load_signed_model(tmp_path, monkeypatch):
    monkeypatch.delenv("HIVE_ALLOW_UNSIGNED_MODEL", raising=False)
    pytest.importorskip("cryptography")
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from hive.model_registry import ModelRegistry

    p = _fit()
    out = tmp_path / "m.joblib"
    p.save(out)
    ModelRegistry.sign_model(out, Ed25519PrivateKey.generate())
    q = CPURouterPolicy.load(out)
    assert q.predict({"listed": False})["tool"] == "list_files"


def test_load_rejects_tampered_signed_model(tmp_path, monkeypatch):
    monkeypatch.delenv("HIVE_ALLOW_UNSIGNED_MODEL", raising=False)
    pytest.importorskip("cryptography")
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from hive.model_registry import ModelRegistry, UnsignedModelError

    p = _fit()
    out = tmp_path / "m.joblib"
    p.save(out)
    ModelRegistry.sign_model(out, Ed25519PrivateKey.generate())
    # Tamper: any change to the model bytes breaks the signed digest.
    with open(out, "ab") as fh:
        fh.write(b"tampered")
    with pytest.raises(UnsignedModelError):
        CPURouterPolicy.load(out)
