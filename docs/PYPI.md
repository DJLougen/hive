# PyPI Publishing

Hive publishes **`hive-agent-memory`** to [PyPI](https://pypi.org/project/hive-agent-memory/) on every tagged release (`v*`).

## Install

```bash
pip install hive-agent-memory
pip install "hive-agent-memory[full]"          # + busybee-cpu + honey-comb
pip install "hive-agent-memory[observability]"
```

## One-time publisher setup

### 1. Create the PyPI project

1. Register at [pypi.org](https://pypi.org/account/register/) (if needed).
2. Create a project named **`hive-agent-memory`** (must match `pyproject.toml`).

### 2. Enable trusted publishing (recommended)

In the PyPI project → **Publishing** → **Add a new pending publisher**:

| Field | Value |
|---|---|
| PyPI project name | `hive-agent-memory` |
| Owner | `DJLougen` |
| Repository name | `hive` |
| Workflow name | `Release` |
| Environment name | `pypi` |

In GitHub → **Settings → Environments** → create environment **`pypi`** (no secrets required when using OIDC).

### 3. Sibling packages

The `[full]` extra depends on:

| Package | PyPI name | Repo |
|---|---|---|
| CPU action router | `busybee-cpu` | [busyBee-cpu](https://github.com/DJLougen/busyBee-cpu) |
| ML compressor | `honey-comb` | [honey-comb](https://github.com/DJLougen/honey-comb) |
| Native backend | `hive-cpp` | bundled in this repo at `hive-cpp/` |

Publish siblings from their own repos with the same trusted-publishing pattern before `pip install "hive-agent-memory[full]"` works end-to-end.

### 4. Cut a release

```bash
# On main, after CHANGELOG and version bumps land:
git tag v0.6.1
git push origin v0.6.1
```

The [Release workflow](.github/workflows/release.yml) will:

1. Build wheels (Linux / macOS / Windows) and an sdist
2. Upload artifacts to the GitHub Release
3. Publish to PyPI via OIDC

### Manual upload (fallback)

```bash
python -m pip install build twine
python -m build
twine upload dist/*
```

Use a PyPI API token only if trusted publishing is unavailable.
