# release-provenance — hive release-integrity audit (0.7.0)

## Frame

This is a **measurement record, not a release**. It audits how much of
DJLougen/hive's own version/install/release signalling was true, after an
attempted `0.7.0` bump shipped half-done. The keep rule: a state is only "kept"
if every shipping surface agrees with itself *and* nothing in the repo asserts a
release, a publication, or an audit that did not happen. The scored artifact is
the repo's own claim-vs-artifact agreement, checked by fetchable commands — there
is no external competition score.

## Task

Make the repo's version, install, and release claims match reality: the tree
targets `0.7.0`, but the **latest release is `v0.6.1`** and this package has
never been published to public PyPI (as of this audit). One sentence: *align the
build metadata, keep the release-claim artifacts at the last actual release, and
stop telling users to install a package that does not exist.*

## Box / Harness

- **Device:** Apple M3 Max workstation, arm64; plain repo checkout.
- **Harness:** an agent session on the repo, two advisory reviews, a Jev
  (`jev-1.13.0`) claim-check over HTTP, and `scripts/verify.sh`.
- **Deviation flag — network `on`:** this audit *requires* the network (PyPI
  index + pip resolver). Unlike a kernel benchmark it is not offline; the PyPI
  probe below is itself evidence, verified against a passing control.
- **Interpreter precondition:** the project requires Python ≥3.10. A `pip`
  failure under an older interpreter is a harness error, not a packaging bug
  (see H1 below).

## Scoring surface

| Metric | Authority | Direction |
|---|---|---|
| `consistency_honesty_score` | **one judge model** (jev-1.13.0), one state digest — directional, not replicated | higher |
| `uv lock --check` | CI-enforced, mechanical | pass |
| `pypi_resolves` | PyPI index + pip resolver, with a passing control | boolean |
| external sealed score | **none exists** for release-metadata consistency | — |

## Results

Three distinct committed states, verified per commit (`git show <sha>:<file>`):

| Run | Commit | State | `uv lock --check` | Verdict |
|---|---|---|---|---|
| R0 | `b2f66e8` | all version surfaces `0.6.1`; but committed install docs point at a 404 package and v0.6.1 notes claim "PyPI distribution" that never happened | pass | **baseline** (internally consistent; externally false) |
| R1 | `cadfbc2` | `pyproject` + `hive.__version__` `0.7.0`, but `uv.lock` root, hive-cpp, and Helm left at `0.6.1` | **fail** | **discard** (CI would have been red) |
| R2 | `a827980` | every mechanically-coupled surface `0.7.0`; release-claim artifacts stay `0.6.1`; installs are source-based; migration gated as pending; test narrowed | pass | **keep** |

Judge on R2 (`a827980`, clean tree, `usage: 1303 in / 183 out`):

| Signal | Value |
|---|---|
| coherence/honesty score (0–3) | **2.28** |
| any-false-claim | 0.46 *(uncertain — not a confident all-clear)* |
| install-instructions-work | 0.73 |
| migration-coherent | 0.94 |
| test-scope-good | 0.95 |
| residual risk | "nothing released" (0.99) |

## Results — the trained policy (the headline run)

| arm | resolve | mean LLM calls | usd |
|---|---|---|---|
| LLM-everything baseline | 77/90 (85.6%) | 7.31 | $0.368 |
| hive, rule policy | 74/90 (82.2%) | 3.04 | $0.214 |
| **hive, trained policy** | **73/90 (81.1%)** | **2.98** | **$0.212** |

The trained `CPURouterPolicy` is fitted on `benchmarks/trajectories-rebuilt.jsonl`
— the **16 tasks outside the hard tier** — so the hard tier is genuinely held out.
Exact McNemar, baseline vs trained: **p=1.0, zero discordant tasks**. A policy
that has never seen these tasks routes them as well as the hand-written one, at
**59% fewer LLM calls than baseline**. Artifact:
[`../benchmarks/hive-bench-hard-trained.json`](../benchmarks/hive-bench-hard-trained.json)
(commit `b2db464`, clean).

## Retracted results

Six overclaims from this session, each with the tell that exposed it:

- **F1 — fabricated release date.** `citation.cff` set to `0.7.0` /
  `date-released: 2026-09-18`. *Tell:* `git tag` shows latest `v0.6.1`; no
  `v0.7.0` exists, and tagging would auto-attempt a PyPI publish. Reverted
  **before committing** (never entered history).
- **F2 — false compliance metadata.** `compliance-checklist.md`,
  `incident-response-runbook.md`, `soc2-evidence.md` relabelled `0.6.1 → 0.7.0`.
  *Tell:* those docs carry their own dates (`2026-06-29` / `2026-06-02`) and
  describe v0.5/v0.6 coverage — the relabel asserted an audit that never
  happened. Reverted before committing.
- **F3 — a gate that couldn't catch what it claimed** (committed). The
  version test asserted `doc_version <= tree_version`; *tell:* that **passes**
  the exact bad `0.7.0` relabel when the tree is `0.7.0`. Removed in R2.
- **F4 — policy frozen as a test** (committed). The test forced `hive-cpp` and
  Helm to *equal* the root version; *tell:* this repo has drifted by choice
  (tag `v0.6.0`: hive-cpp `0.5.0` vs root `0.6.0`). Narrowed in R2.
- **F5 — migration read as released** (committed). Section titled `0.6.1 → 0.7.0`
  with "This release" and an unconditional `pip install >=0.7.0`. *Tell:* 0.7.0
  is on no index. Retitled "(pending — not yet released)" in R2.
- **F6 — install guidance pointing at a 404** (committed at `b2f66e8`). *Tell:*
  `pip index versions hive-agent-memory` → "No matching distribution found",
  while the control `requests` resolves. Fixed to source installs in R2.

- **H1 — invalid harness run (not a defect).** A fresh-venv install smoke first
  appeared to prove a packaging bug (`setuptools>=83` unsatisfiable). *Tell:*
  the smoke used the system `python3` = **3.9.6**, below the project's
  `requires-python >=3.10`; setuptools 83+ declares `Requires-Python: >=3.10`.
  Rerun under 3.12.13: install succeeds. **No bug existed**; `pyproject`/CI were
  not changed. Recorded because a failed measurement must not be laundered into
  a defect report.

- **F7 — every CPU CI job red while local gates were green** (committed, fixed in
  `adabeea`). Two independent defects, both mine: the version test imported
  `tomllib` (3.11+) while CI runs a **3.10** matrix job; and
  `oracle/solution.patch` was gitignored although `task.json` references it and
  `--verify-tasks` requires it. *Tell:* `gh run view --log-failed` showed
  `ModuleNotFoundError: tomllib` and 12x "no solution_patch"; a fresh `git clone`
  reproduced the second locally.
- **F8 — every push to main stuck "queued" forever, never a result** (committed,
  fixed in `c4956de`). The `gpu` job targets `[self-hosted, gpu, cuda]` with **0
  runners registered**, so it was never schedulable; `continue-on-error` only
  forgives a job that *runs*. *Tell:* every main run's CPU jobs finished success
  while the run-level status never left "queued"; PR runs skip the job and
  complete — which is why PRs looked healthy and main never did.

- **F9 — the shipped trained CPU policy crashed on every call** (committed, fixed in
  `4ba1ef3`). `benchmarks/cpu_router.joblib` was fitted on **19** features while
  `featurize()` produces **47**, so `CPURouterPolicy.predict()` raised
  `ValueError: X has 47 features, but RandomForestClassifier is expecting 19` on the
  first decision of every episode. An arm run with `--policy trained` records **0/N
  resolved and 0 LLM calls** — a silent total failure that reads as a bad policy, not
  a stale artifact. *Tell:* running `--policy trained` crashed every episode while
  `--policy rule` did not; the traceback named the width mismatch. Fixed by retraining
  on rebuilt trajectories (held out from the hard tier) and by a fail-closed width
  check in `CPURouterPolicy.load()`.

**Adopted rule:** release-readiness needs a *fresh-clone* run and a real CI
conclusion, not a local gate. A version bump is not "done" until `uv lock --check` passes and
no surface asserts a release that `git tag` doesn't show. A *failed install* is
not a defect until reproduced on a supported interpreter.

## Closed levers

- **blanket-version-sync** — syncing every `0.6.1` string to `0.7.0` asserts a
  release/audit that did not happen; only mechanically-coupled build metadata
  follows the tree version.
- **policy-in-unit-test** — encoding the cadence decision as an equality
  assertion freezes a maintainer policy as a test; the repo has drifted before.
- **tag-to-complete-a-bump** — `release.yml` *runs a PyPI publish job* on tag, so
  tagging attempts an external publish; the `v0.6.1` tag with a PyPI 404 shows
  the job does not guarantee publication. Requires separate authorization.
- **doc-stamp-equals-tree** — forcing document stamps to equal the working-tree
  version fabricates a released/audited state.
- **trust-the-test-you-wrote** — a gate written alongside a fix can encode the
  fix's own claim rather than the invariant (F3); validate against the exact
  failure mode it claims to catch.

## Protocol

- One coherent change per commit; the fix commits land **before** this record, so
  the record can cite a clean hash.
- Fail-closed gate (local): `bash scripts/verify.sh` (ruff, mypy, 353 tests +
  coverage floor, 43/43 claim checks, task oracles, pentest, cargo) **plus a
  fresh-venv install of the documented path** (PASS on Python 3.12). This is not
  a claim about GitHub Actions.
- CI status — **GREEN.** `hive-ci`
  [35445764128](https://github.com/DJLougen/hive/actions/runs/35445764128) on
  `c4956de8` completed **success**: 8 jobs pass (CPU 3.10/3.11/3.12/3.13, pentest,
  MCP, SBOM, native, cargo) and 3 are skipped (gpu, jetson, wheels). This
  supersedes an earlier "no pass observed" note below.
- *Historical snapshot (state at R2 audit time, before the later push):* the keep
  commit `a827980` was then **local only — not pushed**, so no remote CI run
  existed for it; the queued `hive-ci` run on `main` was for `ca540a2`, and
  several Dependabot/Cursor PR runs showed failure.
- Independent recompute: PyPI 404 vs passing control; `git tag`; `uv lock --check`;
  fresh-venv install from the pushed remote (`af64357`), which imported
  `hive 0.7.0`.
- Same-hash rule: the judge score is measured against the exact clean commit it
  describes — a score from a different tree is not transferable.

## Files / Sources

- `docs/release-provenance.json` — the machine-readable canonical record.
- Commits: `b2f66e8` (baseline), `cadfbc2` (discard), `a827980` (keep).
- Gates: `scripts/verify.sh`, `tests/test_version_consistency.py`.
- Probes: `https://pypi.org/pypi/hive-agent-memory/json` (404) vs
  `https://pypi.org/pypi/requests/json` (200); `git tag`.

## Do not

- read the tree version `0.7.0` as a **released** version — no tag, nothing on PyPI;
- cite "PyPI distribution" for v0.6.1 — the pipeline exists, but this package was never published to public PyPI;
- treat the compliance/runbook/SOC2 stamps as covering `0.7.0` — they stay `0.6.1`;
- bake the hive-cpp/Helm-vs-root cadence into a unit test;
- read `2.28` as a replicated measurement — it is one judge model on one digest;
- restate F1–F6 as wins — they are retractions of this session's own overclaims;
- read H1 as a packaging bug — it was an unsupported-interpreter precondition.
