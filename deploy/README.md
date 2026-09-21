# Deploying the Hive API server

`scripts/hive_api_server.py` serves `/route`, `/compress`, `/remember`,
`/recall`, `/health`, `/ready`, and `/openapi.json`. Two manifest sets ship
in this directory:

- `deploy/k8s/` — plain Kubernetes manifests. Do **not** apply the whole
  directory blindly: `secret.yaml` contains a placeholder token that would
  overwrite a real `hive-api` secret. See "Auth" below for the safe apply
  sequence.
- `deploy/helm/` — a Helm chart (`helm install hive deploy/helm --set apiToken=...`).

No published image ships the server today — `ghcr.io/djlougen/hive:latest`
in the manifests is a placeholder you must replace with an image you built.
`docker/Dockerfile.aarch64` builds one, but its build context is the
**parent directory** of this checkout (it COPYs the sibling `busyBee-cpu/`
and `honey-comb/` trees):

```sh
cd .. && docker build -f hive/docker/Dockerfile.aarch64 -t hive:aarch64 .
```

Any custom image must contain this source tree: `pip install .[server]`
installs the `hive` package and FastAPI/uvicorn, but
`scripts/hive_api_server.py` is not a packaged module — the image needs the
checkout (or at least that script) on disk.

## What this is — and is not

This is a **single-process, single-replica** service. Memory lives in the
pod's local `RustBrain`; there is no shared durable store behind it. In
particular:

- **One replica only.** Two pods do not share memory — each serves its own
  divergent state. The Helm chart refuses `replicaCount > 1`; the plain
  manifests pin `replicas: 1`. Do not scale this Deployment.
- **Recreate strategy.** Rollouts terminate the old pod before the new one
  starts. Two overlapping pods would serve divergent memory and race on
  the same snapshot file, so rolling updates are disabled deliberately.
- **Persistence is opt-in and per-pod.** Set `HIVE_MEMORY_SNAPSHOT` to a
  path on a mounted volume to restore memory on startup and persist every
  successful `/remember` atomically (tempfile + `os.replace` in the same
  directory). Without a volume, snapshots die with the pod. A corrupt
  snapshot fails startup closed rather than serving partial state.
  Honest limits: the write is atomic but there is no fsync, so a machine
  or power loss can still lose the last snapshot; a persistence failure
  returns HTTP 500 *after* the in-memory write already happened (it does
  not roll back); and the snapshot contract assumes a single process —
  do not run uvicorn with `--workers > 1`, each worker would hold its own
  divergent memory. The Helm chart does not wire a volume for you — add a
  PVC/volumeMount to the pod spec yourself if you want snapshots to
  survive pod replacement.
- **Not a shared durable memory service.** If you need multi-writer or
  HA memory, that is a different architecture — this chart does not
  provide it.

## Environment

`HiveConfig.from_env()` reads `HIVE_*` vars at startup (rate limit, tenant
isolation, TTL, max nodes — see `hive/config.py`). Server-specific vars:

| Variable | Values | Default | Meaning |
|---|---|---|---|
| `HIVE_ROUTING_POLICY` | `off`, `rule` | `off` | `off` loads no routing policy — every `/route` escalates, nothing paid or unsafe is loaded implicitly. `rule` uses the local rule-based policy. Any other value fails startup. |
| `HIVE_API_TOKEN` | string | unset | Bearer token required on the data endpoints. Unset = open **dev mode** — fine on localhost, never expose it. |
| `HIVE_PRODUCTION` | `1`/`true`/`yes`/`on` | off | Requires `HIVE_API_TOKEN` to be present, non-empty, and non-placeholder; startup fails otherwise. |
| `HIVE_MEMORY_SNAPSHOT` | path | unset | Snapshot restore on boot + atomic persist on each `/remember`. |

## Auth

`/health`, `/ready`, and `/openapi.json` are always public so probes and
docs work. The data endpoints require `Authorization: Bearer <token>` when
`HIVE_API_TOKEN` is set.

The k8s manifests ship a placeholder `HIVE_API_TOKEN: "REPLACE-ME"` in
`deploy/k8s/secret.yaml` **and** set `HIVE_PRODUCTION=true`, so a pod that
starts with the placeholder crash-loops — that fail-closed refusal is the
intended signal. Two safe ways to deploy:

**Option A — secret out-of-band (recommended).** Create the secret
yourself, then apply only the non-secret manifests:

```sh
# --dry-run | apply makes this repeatable (idempotent re-apply):
kubectl create secret generic hive-api \
    --from-literal=HIVE_API_TOKEN="$(openssl rand -hex 32)" \
    --dry-run=client -o yaml | kubectl apply -f -
kubectl apply -f deploy/k8s/configmap.yaml -f deploy/k8s/deployment.yaml
```

**Option B — edit the placeholder locally.** Put a real token in
`deploy/k8s/secret.yaml` (never commit it), then apply all three files
explicitly:

```sh
kubectl apply -f deploy/k8s/configmap.yaml -f deploy/k8s/deployment.yaml \
    -f deploy/k8s/secret.yaml
```

Either way, do not run `kubectl apply -f deploy/k8s/` from a clean
checkout afterward — it would overwrite the live secret with the
placeholder again.

For Helm, pass `--set apiToken=...` (the chart refuses to render without
it, and refuses common placeholder values) or use a values file kept out
of version control — the chart's `templates/secret.yaml` does create the
`<release>-api` Secret from that value, so keep the token source out of
git. Nothing here deploys automatically.

## Local dev

```sh
python scripts/hive_api_server.py --port 8080   # binds 127.0.0.1, dev mode
```

Dev mode is clearly labeled: the server logs a warning, `main()` prints
one, and `/health` reports `"mode": "dev"`.
