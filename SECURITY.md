# Security Policy

## Supported Versions

Hive is currently in **Step 1 (Python meta-package)**. Security fixes are
issued for the latest released minor version and the immediately preceding
one. Earlier versions are best-effort.

| Version | Supported           |
|---------|---------------------|
| 0.5.x   | :white_check_mark:  |
| 0.4.x   | :white_check_mark:  |
| < 0.4   | :x:                 |

## Reporting a Vulnerability

Please **do not** open a public GitHub issue for security-sensitive bugs.
Send a report to **djlougen+hive-security [at] gmail.com** with:

* a description of the vulnerability and the impact you observe,
* a minimal reproducer (transcript, command, or test case),
* the version of `hive` you are running,
* the version of the sibling packages (`busybee-cpu`, `honeycomb`) and
  Python you are running.

You can expect an acknowledgement within 72 hours. We aim to ship a fix
within 14 days for critical issues and 30 days for moderate ones. The
reporter is credited in the CHANGELOG unless they ask to remain anonymous.

## Scope

The Hive meta-package itself is a thin orchestrator. The main attack
surfaces are:

* `hive.llm` — outbound HTTP to vLLM / llama.cpp servers. Sanity-check
  endpoints and never log full message bodies.
* `hive.rust_brain` — monotonic-timestamp guard rejects replays of
  older writes. The trust score is the user-controlled input; do not
  treat high-trust nodes as authoritative in a multi-tenant setting.
* `hive.hardware` — pynvml is read-only; no attack surface.
* `scripts/hive_api_server.py` — REST API; set `HIVE_REQUIRE_AUTH=true` and
  configure JWT before exposing beyond localhost.
* `scripts/hive_mcp_server.py` — MCP tools (stdio = local trust; SSE needs auth).
* `hive.gossip` — cross-node memory sync; set `HIVE_GOSSIP_SECRET` on receivers.

Report issues in **busybee-cpu** or **honey-comb** to the same security
contact; fixes may land in the sibling repo and be pulled into Hive releases.

## Self-service penetration testing

Modular checks cover each installable component. Siblings are optional;
skipped modules print install instructions.

```bash
# Hive core + HTTP/MCP/gossip (matches default CI on PRs)
pip install -e ".[dev]"
python scripts/hive_pentest.py --module hive --module api --module mcp --module gossip

# Production profile (validates auth hooks exist)
python scripts/hive_pentest.py --profile prod --module api --module mcp --module gossip

# Active HTTP checks (FastAPI TestClient)
python scripts/hive_pentest.py --active --module api

# Full stack (busyBee-cpu + honey-comb side-by-side)
git clone https://github.com/DJLougen/busyBee-cpu ../busyBee-cpu
git clone https://github.com/DJLougen/honey-comb ../honey-comb
pip install -e ../busyBee-cpu ../honey-comb -e ".[dev]"
python scripts/hive_pentest.py --profile prod --fail-on-skip

python -m bandit -r hive/ scripts/hive_api_server.py scripts/hive_mcp_server.py -ll
pip-audit
```

| Module | Package | Focus |
|--------|---------|--------|
| `hive` | `hive` | JWT, tenancy, health bind, feedback poisoning, LLM URLs |
| `api` | `scripts/hive_api_server.py` | localhost bind, JWT middleware, 413 on oversized compress |
| `mcp` | `scripts/hive_mcp_server.py` | SSE bind, JWT middleware, tool surface, stdio trust |
| `gossip` | `hive.gossip` | http(s) peers only, `HIVE_GOSSIP_SECRET` on receive |
| `busybee` | `busybee_cpu` | joblib trust, `/v1/learn`, CORS, body limits, predict DoS |
| `honeycomb` | `honeycomb` | model fallback, CORE system prompts, tee paths, large inputs |
| `integration` | all three | `HiveStack` wired to real busybee + honeycomb |

Production environment variables:

| Variable | Surface | Purpose |
|----------|---------|---------|
| `HIVE_REQUIRE_AUTH=true` | REST API, MCP SSE | Require `Authorization: Bearer <jwt>` |
| `HIVE_JWKS_URL` / `HIVE_JWT_PUBLIC_KEY` | JWT validation | Identity provider |
| `HIVE_GOSSIP_SECRET` | Gossip receive | Shared secret for cross-node memory sync |

For Kubernetes deployments, set `HIVE_HEALTH_BIND=0.0.0.0` only inside the
pod network; the default is loopback (`127.0.0.1`). Always configure
`HIVE_JWKS_URL` or `HIVE_JWT_PUBLIC_KEY` before calling `JWTValidator.validate()`.

Never expose **bee-serve** (`busybee_cpu.server`) to untrusted networks without
authentication. Apply `patches/busybee-secure-learn.patch` upstream (or set
`BUSYBEE_LEARN_API_KEY`); `/v1/learn` mutates the routing policy.

**Active pentest** (exploit probes):

```bash
python scripts/hive_pentest.py --active --fail-on-skip
```

Known residual risk: **joblib/pickle** model files are executable if tampered —
only load `.joblib` from trusted, signed distribution paths.
