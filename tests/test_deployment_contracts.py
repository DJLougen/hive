"""Deployment contracts for the Hive API server and release workflows.

Covers the env-driven server surface (HiveConfig.from_env, routing policy
selection, snapshot persistence, production token gate) and the CI/release
workflow wiring that decides whether artifacts can ship.
"""

from __future__ import annotations

import gzip
import re
from pathlib import Path

import pytest

# Only a genuinely missing optional dependency may skip these tests.
# api_server is imported unconditionally under the real environment: a
# broken hive/scripts import or a module-level startup failure (invalid
# config, corrupt snapshot, bad env) must fail collection loudly — never
# masquerade as "fastapi not installed". The module itself tolerates a
# missing fastapi/uvicorn via its own _HAS_FASTAPI flag.
import scripts.hive_api_server as api_server

try:
    from fastapi.testclient import TestClient
except ModuleNotFoundError as exc:  # pragma: no cover - optional `server` extra
    if exc.name not in {"fastapi", "starlette", "httpx", "httpcore", "anyio"}:
        raise
    TestClient = None  # type: ignore[assignment]

pytestmark_server = pytest.mark.skipif(
    TestClient is None or not api_server._HAS_FASTAPI,
    reason="optional server dependencies not installed (pip install -e .[server])",
)

REPO_ROOT = Path(__file__).resolve().parent.parent
CI_YML = (REPO_ROOT / ".github/workflows/ci.yml").read_text()
RELEASE_YML = (REPO_ROOT / ".github/workflows/release.yml").read_text()


def _build_stack(env: dict[str, str], monkeypatch: pytest.MonkeyPatch):
    """Build a stack with a controlled environment (server vars + config)."""
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return api_server.build_stack(env)


# ---------------------------------------------------------------------------
# Step 1+2: HiveConfig.from_env + HIVE_ROUTING_POLICY
# ---------------------------------------------------------------------------


@pytestmark_server
def test_build_stack_consumes_hive_config_from_env(monkeypatch):
    stack = _build_stack({"HIVE_RATE_LIMIT": "7"}, monkeypatch)
    assert stack.config.rate_limit == 7
    assert stack.rate_limiter is not None


@pytestmark_server
def test_invalid_hive_config_fails_startup(monkeypatch):
    with pytest.raises(ValueError, match="rate_limit"):
        _build_stack({"HIVE_RATE_LIMIT": "-1"}, monkeypatch)


@pytestmark_server
def test_routing_policy_off_loads_no_policy(monkeypatch):
    stack = _build_stack({"HIVE_ROUTING_POLICY": "off"}, monkeypatch)
    assert stack.busybee is None


@pytestmark_server
def test_routing_policy_defaults_to_off(monkeypatch):
    monkeypatch.delenv("HIVE_ROUTING_POLICY", raising=False)
    stack = api_server.build_stack()
    assert stack.busybee is None


@pytestmark_server
def test_routing_policy_rule_uses_local_rule_policy(monkeypatch):
    from hive.harness import RuleBasedRoutingPolicy

    stack = _build_stack({"HIVE_ROUTING_POLICY": "rule"}, monkeypatch)
    assert isinstance(stack.busybee, RuleBasedRoutingPolicy)


@pytestmark_server
def test_routing_policy_invalid_fails_closed(monkeypatch):
    with pytest.raises(RuntimeError, match="HIVE_ROUTING_POLICY"):
        _build_stack({"HIVE_ROUTING_POLICY": "paid-model"}, monkeypatch)


@pytestmark_server
def test_route_escalates_when_policy_off(monkeypatch):
    stack = _build_stack({"HIVE_ROUTING_POLICY": "off"}, monkeypatch)
    client = TestClient(api_server.create_app(stack))
    resp = client.post("/route", json={"goal": "read file", "step": 0})
    assert resp.status_code == 200
    body = resp.json()
    assert body["escalated"] is True
    assert body["source"] == "fallback"


# ---------------------------------------------------------------------------
# Step 3: /remember propagates write rejection
# ---------------------------------------------------------------------------


@pytestmark_server
def test_remember_returns_error_when_stack_rejects_write(monkeypatch):
    stack = _build_stack({}, monkeypatch)

    def reject(*args, **kwargs):
        raise RuntimeError("store full")

    monkeypatch.setattr(stack, "remember", reject)
    client = TestClient(api_server.create_app(stack))
    resp = client.post("/remember", json={"key": "k", "value": "v"})
    assert resp.status_code == 500
    assert "rejected" in resp.json()["detail"]


@pytestmark_server
def test_remember_ok_on_success(monkeypatch):
    stack = _build_stack({}, monkeypatch)
    client = TestClient(api_server.create_app(stack))
    resp = client.post("/remember", json={"key": "k", "value": {"a": 1}})
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
    assert client.get("/recall", params={"key": "k"}).json()["value"] == {"a": 1}


# ---------------------------------------------------------------------------
# Step 4+5: snapshot restore (fail closed) + atomic durable writes
# ---------------------------------------------------------------------------


@pytestmark_server
def test_snapshot_restored_on_startup(tmp_path, monkeypatch):
    seed = _build_stack({}, monkeypatch)
    seed.remember("persisted", {"n": 42})
    snap = tmp_path / "brain.snapshot.gz"
    seed.brain.snapshot_to_file(str(snap))

    stack = _build_stack({"HIVE_MEMORY_SNAPSHOT": str(snap)}, monkeypatch)
    assert stack.recall("persisted") == {"n": 42}


@pytestmark_server
def test_corrupt_snapshot_fails_startup_closed(tmp_path, monkeypatch):
    snap = tmp_path / "brain.snapshot.gz"
    snap.write_bytes(b"not a valid gzip snapshot")
    with pytest.raises(Exception):
        _build_stack({"HIVE_MEMORY_SNAPSHOT": str(snap)}, monkeypatch)


@pytestmark_server
def test_tampered_snapshot_checksum_fails_startup(tmp_path, monkeypatch):
    seed = _build_stack({}, monkeypatch)
    seed.remember("k", "v")
    snap = tmp_path / "brain.snapshot.gz"
    seed.brain.snapshot_to_file(str(snap))
    # Flip bytes inside the gzip payload so the embedded checksum fails.
    raw = bytearray(snap.read_bytes())
    raw[-1] ^= 0xFF
    snap.write_bytes(bytes(raw))
    with pytest.raises(Exception):
        _build_stack({"HIVE_MEMORY_SNAPSHOT": str(snap)}, monkeypatch)


@pytestmark_server
def test_successful_remember_persists_snapshot(tmp_path, monkeypatch):
    snap = tmp_path / "brain.snapshot.gz"
    stack = _build_stack({"HIVE_MEMORY_SNAPSHOT": str(snap)}, monkeypatch)
    client = TestClient(
        api_server.create_app(stack, snapshot_path=str(snap))
    )
    resp = client.post("/remember", json={"key": "durable", "value": 7})
    assert resp.status_code == 200
    assert snap.exists()

    fresh = _build_stack({"HIVE_MEMORY_SNAPSHOT": str(snap)}, monkeypatch)
    assert fresh.recall("durable") == 7


@pytestmark_server
def test_snapshot_write_failure_returns_error(tmp_path, monkeypatch):
    snap = tmp_path / "brain.snapshot.gz"
    stack = _build_stack({"HIVE_MEMORY_SNAPSHOT": str(snap)}, monkeypatch)

    def fail_save(path):
        raise OSError("disk full")

    monkeypatch.setattr(stack.brain, "snapshot_to_file", fail_save)
    client = TestClient(
        api_server.create_app(stack, snapshot_path=str(snap))
    )
    resp = client.post("/remember", json={"key": "k", "value": "v"})
    assert resp.status_code == 500
    assert "persistence failed" in resp.json()["detail"]


@pytestmark_server
def test_atomic_save_uses_tempfile_and_replace(tmp_path, monkeypatch):
    """Writes go tempfile→os.replace in the snapshot's own directory."""
    snap = tmp_path / "sub" / "brain.snapshot.gz"
    stack = _build_stack({"HIVE_MEMORY_SNAPSHOT": str(snap)}, monkeypatch)
    client = TestClient(
        api_server.create_app(stack, snapshot_path=str(snap))
    )
    assert client.post("/remember", json={"key": "a", "value": 1}).status_code == 200
    # No temp files leak in the snapshot directory.
    leftovers = [p.name for p in snap.parent.iterdir() if p.name != snap.name]
    assert leftovers == []
    # And the file is a valid restorable snapshot.
    assert gzip.decompress(snap.read_bytes())


# ---------------------------------------------------------------------------
# Step 7: production flag requires a real token; dev mode stays open
# ---------------------------------------------------------------------------


@pytestmark_server
def test_production_requires_token():
    with pytest.raises(RuntimeError, match="HIVE_API_TOKEN"):
        api_server._resolve_api_token({"HIVE_PRODUCTION": "true"})
    with pytest.raises(RuntimeError, match="HIVE_API_TOKEN"):
        api_server._resolve_api_token(
            {"HIVE_PRODUCTION": "1", "HIVE_API_TOKEN": "  "}
        )


@pytestmark_server
@pytest.mark.parametrize("placeholder", ["REPLACE-ME", "changeme", "secret"])
def test_production_rejects_placeholder_token(placeholder):
    with pytest.raises(RuntimeError, match="placeholder"):
        api_server._resolve_api_token(
            {"HIVE_PRODUCTION": "true", "HIVE_API_TOKEN": placeholder}
        )


@pytestmark_server
def test_production_accepts_real_token():
    token = api_server._resolve_api_token(
        {"HIVE_PRODUCTION": "true", "HIVE_API_TOKEN": "a" * 32}
    )
    assert token == "a" * 32


@pytestmark_server
def test_dev_mode_is_open_and_labelled(monkeypatch):
    stack = _build_stack({}, monkeypatch)
    client = TestClient(api_server.create_app(stack, api_token=None))
    assert client.get("/health").json()["mode"] == "dev"
    # Data endpoints reachable without a token in dev mode.
    assert client.post("/remember", json={"key": "k", "value": 1}).status_code == 200


@pytestmark_server
def test_production_flag_rejects_unknown_boolean():
    """A typo like HIVE_PRODUCTION=ture must fail, not silently open the API."""
    with pytest.raises(RuntimeError, match="HIVE_PRODUCTION"):
        api_server._resolve_api_token({"HIVE_PRODUCTION": "ture"})
    # Explicit false forms and unset stay dev mode.
    assert api_server._resolve_api_token({"HIVE_PRODUCTION": "false"}) is None
    assert api_server._resolve_api_token({}) is None


@pytestmark_server
def test_token_gates_data_endpoints(monkeypatch):
    stack = _build_stack({}, monkeypatch)
    client = TestClient(api_server.create_app(stack, api_token="tok"))
    assert client.get("/health").json()["mode"] == "production"
    assert client.post("/remember", json={"key": "k", "value": 1}).status_code == 401
    assert (
        client.post(
            "/remember",
            json={"key": "k", "value": 1},
            headers={"Authorization": "Bearer tok"},
        ).status_code
        == 200
    )
    # Probes stay public.
    assert client.get("/health").status_code == 200


# ---------------------------------------------------------------------------
# Step 6: deploy manifests — single replica, Recreate, Helm fail >1
# ---------------------------------------------------------------------------


def test_k8s_deployment_single_replica_recreate():
    text = (REPO_ROOT / "deploy/k8s/deployment.yaml").read_text()
    assert re.search(r"^\s+replicas:\s*1\s*$", text, re.M)
    assert "type: Recreate" in text
    assert "replicas: 2" not in text


def test_helm_defaults_single_replica_recreate():
    values = (REPO_ROOT / "deploy/helm/values.yaml").read_text()
    assert re.search(r"^replicaCount:\s*1\s*$", values, re.M)
    template = (REPO_ROOT / "deploy/helm/templates/deployment.yaml").read_text()
    assert "type: Recreate" in template


def test_helm_fails_on_multiple_replicas():
    template = (REPO_ROOT / "deploy/helm/templates/deployment.yaml").read_text()
    assert "fail" in template
    assert "replicaCount" in template
    # The guard must compare replicaCount against 1.
    assert re.search(r"gt\s+\(int\s+\.Values\.replicaCount\)\s+1", template)


def test_deploy_docs_state_persistence_limits():
    doc = (REPO_ROOT / "deploy/README.md").read_text()
    assert "single" in doc.lower() and "replica" in doc.lower()
    assert "Recreate" in doc
    assert "HIVE_MEMORY_SNAPSHOT" in doc
    # Must not claim a shared durable service.
    assert "not a shared durable" in doc.lower() or "no shared durable" in doc.lower()



def test_helm_secret_rejects_placeholder_token():
    template = (REPO_ROOT / "deploy/helm/templates/secret.yaml").read_text()
    assert "required" in template  # empty apiToken still fails to render
    assert "fail" in template and "replace-me" in template


def test_k8s_secret_is_placeholder_and_production_gated():
    secret = (REPO_ROOT / "deploy/k8s/secret.yaml").read_text()
    deployment = (REPO_ROOT / "deploy/k8s/deployment.yaml").read_text()
    assert "REPLACE-ME" in secret
    assert "HIVE_PRODUCTION" in deployment


def _configmap_keys() -> set[str]:
    """HIVE_* keys defined in deploy/k8s/configmap.yaml's data block."""
    text = (REPO_ROOT / "deploy/k8s/configmap.yaml").read_text()
    data = re.search(r"^data:\n((?:[ \t].*\n?)*)", text, re.M)
    assert data, "configmap.yaml missing data: block"
    return set(re.findall(r"^\s+(HIVE_[A-Z_]+):", data.group(1), re.M))


def test_k8s_deployment_consumes_configmap():
    """The ConfigMap must actually reach the pod, and inline env must not
    silently shadow its keys (a duplicated key would drift from the map)."""
    deployment = (REPO_ROOT / "deploy/k8s/deployment.yaml").read_text()
    assert "envFrom:" in deployment, "deployment does not consume any ConfigMap"
    assert re.search(r"configMapRef:\s*\n\s*name:\s*hive-config", deployment), (
        "deployment must envFrom the hive-config ConfigMap"
    )
    inline = set(re.findall(r"-\s*name:\s*(HIVE_\w+)", deployment))
    overlap = inline & _configmap_keys()
    assert not overlap, f"inline env duplicates ConfigMap keys: {overlap}"


def test_configmap_keys_are_real_hive_config_fields():
    """Every ConfigMap key must name a real HiveConfig field — a typo'd key
    would be silently ignored by HiveConfig.from_env()."""
    from hive.config import HiveConfig

    valid = {f"HIVE_{name.upper()}" for name in HiveConfig.__dataclass_fields__}
    unknown = _configmap_keys() - valid
    assert not unknown, f"ConfigMap keys that are not HiveConfig fields: {unknown}"


def test_helm_env_keys_are_real_hive_config_fields():
    """values.yaml `env` keys map to HiveConfig fields; `serverEnv` keys are
    server-specific and must NOT be HiveConfig fields (wrong bucket = drift)."""
    from hive.config import HiveConfig

    values = (REPO_ROOT / "deploy/helm/values.yaml").read_text()
    valid = {f"HIVE_{name.upper()}" for name in HiveConfig.__dataclass_fields__}

    env_block = re.search(r"^env:\n((?:[ \t].*\n?)*)", values, re.M)
    assert env_block, "values.yaml missing env: block"
    env_keys = set(re.findall(r"^\s+(HIVE_[A-Z_]+):", env_block.group(1), re.M))
    assert env_keys, "values.yaml env: block has no HIVE_* keys"
    assert not (env_keys - valid), (
        f"values env keys that are not HiveConfig fields: {env_keys - valid}"
    )

    server_block = re.search(r"^serverEnv:\n((?:[ \t].*\n?)*)", values, re.M)
    assert server_block, "values.yaml missing serverEnv: block"
    server_keys = set(
        re.findall(r"^\s+(HIVE_[A-Z_]+):", server_block.group(1), re.M)
    )
    assert server_keys, "values.yaml serverEnv: block has no HIVE_* keys"
    assert not (server_keys & valid), (
        f"serverEnv keys that are HiveConfig fields (belong in env): "
        f"{server_keys & valid}"
    )


def test_deploy_docs_no_blanket_apply_of_secret_dir():
    """Applying the whole deploy/k8s/ directory would overwrite a real
    hive-api secret with the REPLACE-ME placeholder — the docs must only
    ever show explicit per-file applies."""
    doc = (REPO_ROOT / "deploy/README.md").read_text()
    assert not re.search(r"kubectl apply[^\n]*-f\s+deploy/k8s/?\s*$", doc, re.M), (
        "docs must not recommend `kubectl apply -f deploy/k8s/` — it applies "
        "the placeholder secret.yaml over a real secret"
    )


# ---------------------------------------------------------------------------
# Step 8: CI tag trigger makes the native wheel job reachable
# ---------------------------------------------------------------------------


def test_ci_triggers_on_version_tags():
    on_block = re.search(r"^on:\n(.*?)^\S", CI_YML, re.M | re.S)
    assert on_block, "ci.yml missing on: block"
    assert re.search(r"tags:\s*\[?'?v\*'?\]?", on_block.group(1))


def test_ci_rust_wheels_job_is_tag_gated():
    job = _job_block(CI_YML, "rust-wheels")
    assert "refs/tags/v" in job


# ---------------------------------------------------------------------------
# Step 9+10: release validation gates publishing; exact local wheel install
# ---------------------------------------------------------------------------


def _job_block(yaml_text: str, job: str) -> str:
    m = re.search(rf"^  {re.escape(job)}:\n(.*?)(?=^  \S|\Z)", yaml_text, re.M | re.S)
    assert m, f"{job} job missing"
    return m.group(1)


def test_release_validation_job_gates_pypi_publish():
    publish = _job_block(RELEASE_YML, "publish-pypi")
    assert re.search(r"needs:.*validate-release", publish)


def test_github_release_depends_on_pypi_publish():
    release = _job_block(RELEASE_YML, "create-release")
    assert re.search(r"needs:.*publish-pypi", release)
    # And must not bypass PyPI by depending only on the build jobs.
    needs_line = re.search(r"needs:\s*\[([^\]]*)\]", release)
    assert needs_line and "build-wheels" not in needs_line.group(1)


def test_release_installs_exact_local_wheel():
    validate = _job_block(RELEASE_YML, "validate-release")
    # Exact wheel file from dist/, not a resolver lookup that can fall back
    # to a previously published PyPI version.
    assert re.search(r"pip install\s+dist/[^\s]*\.whl", validate)
    assert "--find-links" not in validate


def test_release_publish_step_uses_pypi_action():
    publish = _job_block(RELEASE_YML, "publish-pypi")
    assert "gh-action-pypi-publish" in publish
