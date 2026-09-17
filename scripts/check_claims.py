#!/usr/bin/env python3
"""Claim gate — every number Hive publishes in README.md must match a committed artifact.

Each registered check pulls one number out of README.md with a regex and compares it
against a value read from a committed benchmark artifact. Rounded display values are
accepted within tolerance (relative 1e-3, absolute 0.05).

Exit codes: 0 = all checks OK, 1 = at least one MISSING or MISMATCH.

Stdlib only; no network.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
README_PATH = ROOT / "README.md"
REL_TOL = 1e-3
ABS_TOL = 0.05

# --------------------------------------------------------------------------- #
# Registry
#
#   kind="pair"   -> readme groups: baseline, hive, delta (percent)
#   kind="single" -> readme group 1 vs the artifact value
# --------------------------------------------------------------------------- #

BENCH = "docs/benchmarks/hive-bench-flash.json"
MICRO = "docs/benchmarks/latest-micro.json"
SMOKE = "docs/benchmarks/long-context-smoke.json"
BAKEOFF = "docs/benchmarks/trace-bakeoff.json"

CHECKS: list[dict] = [
    # ---- A1: real-workload A/B table (summary of hive-bench-flash.json) ----
    {
        "label": "A1 resolve rate (count)",
        "kind": "pair",
        "artifact": BENCH,
        "baseline": "summary.baseline.resolved",
        "hive": "summary.hive.resolved",
        "regex": r"^\| Resolve rate \| 100% \((10)/10\) \| 100% \((10)/10\) \| \*\*0 pp\*\*",
    },
    {
        "label": "A1 mean LLM calls",
        "kind": "pair",
        "artifact": BENCH,
        "baseline": "summary.baseline.mean_llm_calls",
        "hive": "summary.hive.mean_llm_calls",
        "delta_sign": -1,
        "regex": r"^\| Mean LLM calls \| ([\d.]+) \| ([\d.]+) \| \*\*[−-]([\d.]+)%\*\*",  # noqa: RUF001
    },
    {
        "label": "A1 mean prompt tokens",
        "kind": "pair",
        "artifact": BENCH,
        "baseline": "summary.baseline.mean_prompt_tokens",
        "hive": "summary.hive.mean_prompt_tokens",
        "delta_sign": -1,
        "regex": r"^\| Mean prompt tokens \| ([\d,]+) \| ([\d,]+) \| \*\*[−-]([\d.]+)%\*\*",  # noqa: RUF001
    },
    {
        "label": "A1 mean completion tokens",
        "kind": "pair",
        "artifact": BENCH,
        "baseline": "summary.baseline.mean_completion_tokens",
        "hive": "summary.hive.mean_completion_tokens",
        "delta_sign": -1,
        "regex": r"^\| Mean completion tokens \| ([\d,]+) \| ([\d,]+) \| \*\*[−-]([\d.]+)%\*\*",  # noqa: RUF001
    },
    {
        "label": "A1 mean turns",
        "kind": "pair",
        "artifact": BENCH,
        "baseline": "summary.baseline.mean_turns",
        "hive": "summary.hive.mean_turns",
        "delta_sign": 1,
        "regex": r"^\| Mean turns \| ([\d.]+) \| ([\d.]+) \| \+([\d.]+)%",
    },
    {
        "label": "A1 mean wall clock",
        "kind": "pair",
        "artifact": BENCH,
        "baseline": "summary.baseline.mean_wall_clock_s",
        "hive": "summary.hive.mean_wall_clock_s",
        "delta_sign": -1,
        "regex": r"^\| Mean wall clock \(s\) \| ([\d.]+) \| ([\d.]+) \| \*\*[−-]([\d.]+)%\*\*",  # noqa: RUF001
    },
    {
        "label": "A1 memory recall hits",
        "kind": "single",
        "artifact": BENCH,
        "path": "summary.hive.memory_hits",
        "regex": r"^\| Memory recall hits \| — \| (\d+)/10 \|",
    },
    # ---- A2: per-component throughput (latest-micro.json) ----
    {
        "label": "A2 rust_brain items/s",
        "kind": "single",
        "artifact": MICRO,
        "path": "components[rust_brain].rate_mean_per_s",
        "regex": r"^\| rust_brain \| ([\d,]+) \|",
    },
    {
        "label": "A2 compress[fast] items/s",
        "kind": "single",
        "artifact": MICRO,
        "path": "components[compress[fast]].rate_mean_per_s",
        "regex": r"^\| compress\[fast\] \| ([\d,]+) \|",
    },
    {
        "label": "A2 compress[honeycomb] items/s",
        "kind": "single",
        "artifact": MICRO,
        "path": "components[compress[honeycomb]].rate_mean_per_s",
        "regex": r"^\| compress\[honeycomb\] \| ([\d,]+) \|",
    },
    {
        "label": "A2 busybee_cpu items/s",
        "kind": "single",
        "artifact": MICRO,
        "path": "components[busybee_cpu].rate_mean_per_s",
        # published as a 3-significant-figure table value (112) vs 111.7 in prose
        "rel_tol": 5e-3,
        "regex": r"^\| busybee_cpu \| ([\d,]+) \|",
    },
    # ---- A3: long-context compression ratio ----
    {
        "label": "A3 long-context max_ratio",
        "kind": "single",
        "artifact": SMOKE,
        "path": "max_ratio",
        "regex": r"up to \*\*([\d.]+)×\*\* compression",  # noqa: RUF001
    },
    # ---- A6: trace bake-off accuracy ----
    {
        "label": "A6 mlp next-tool accuracy",
        "kind": "single",
        "artifact": BAKEOFF,
        "path": "models[mlp].argmax_overall.agreement",
        "percent": True,
        "regex": r"\*\*([\d.]+)%\*\* raw next-tool accuracy",
    },
    {
        "label": "A6 repeat-last baseline",
        "kind": "single",
        "artifact": BAKEOFF,
        "path": "models[repeat-last].argmax_overall.agreement",
        "percent": True,
        "regex": r"vs ([\d.]+)% repeat-last",
    },
    {
        "label": "A6 majority baseline",
        "kind": "single",
        "artifact": BAKEOFF,
        "path": "models[majority].argmax_overall.agreement",
        "percent": True,
        "regex": r"and ([\d.]+)% majority",
    },
]


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def resolve(root: object, path: str) -> float:
    """Traverse ``a.b`` / ``a[0]`` / ``components[name]`` paths."""
    cur = root
    for part in path.split("."):
        m = re.match(r"^([^\[\]]+)(?:\[(.+)\])?$", part)
        if not m:
            raise KeyError(path)
        key, sel = m.group(1), m.group(2)
        cur = cur[key]  # type: ignore[index]
        if sel is None:
            continue
        if sel.isdigit():
            cur = cur[int(sel)]  # type: ignore[index]
        elif isinstance(cur, dict) and sel in cur:
            cur = cur[sel]
        else:
            # list-of-dicts containers (e.g. latest-micro.json components) match on "name"
            matches = [c for c in cur if isinstance(c, dict) and c.get("name") == sel]  # type: ignore[union-attr]
            if len(matches) != 1:
                raise KeyError(f"selector {sel!r} in {path!r} matched {len(matches)} entries")
            cur = matches[0]
    return float(cur)  # type: ignore[arg-type]


def load_artifact(path: str) -> object:
    file = ROOT / path
    if not file.exists():
        raise FileNotFoundError(f"{path} is not committed")
    return json.loads(file.read_text())


def num(text: str) -> float:
    return float(text.replace(",", ""))


def close(readme_value: float, artifact_value: float, rel_tol: float = REL_TOL) -> bool:
    return abs(readme_value - artifact_value) <= max(rel_tol * abs(artifact_value), ABS_TOL)


def run_check(check: dict, readme: str) -> list[str]:
    """Return a list of problem strings (empty = pass); prints OK/MISMATCH inline."""
    label = check["label"]
    pattern = re.compile(check["regex"], re.MULTILINE)
    hits = pattern.findall(readme)
    if len(hits) != 1:
        return [f"MISSING\t{label}\tpattern matched {len(hits)} times in README.md"]
    groups = hits[0] if isinstance(hits[0], tuple) else (hits[0],)
    artifact = load_artifact(check["artifact"])
    problems: list[str] = []

    if check["kind"] == "pair":
        readme_baseline, readme_hive = num(str(groups[0])), num(str(groups[1]))
        art_baseline = resolve(artifact, check["baseline"])
        art_hive = resolve(artifact, check["hive"])
        for name, rv, av in (
            (f"{label} [baseline]", readme_baseline, art_baseline),
            (f"{label} [hive]", readme_hive, art_hive),
        ):
            if not close(rv, av):
                problems.append(f"MISMATCH\t{name}\treadme={rv} artifact={av}")
        if "delta_sign" in check:
            readme_delta = num(str(groups[2])) * check["delta_sign"]
            art_delta = (art_hive - art_baseline) / art_baseline * 100
            if not close(readme_delta, art_delta):
                problems.append(f"MISMATCH\t{label} [delta]\treadme={readme_delta} artifact={round(art_delta, 2)}")
        if not problems:
            print(f"OK\t{label}\treadme={readme_baseline}/{readme_hive}\tartifact={art_baseline}/{art_hive}")
        return problems

    readme_value = num(str(groups[0]))
    artifact_value = resolve(artifact, check["path"])
    if check.get("percent"):
        artifact_value *= 100
    if not close(readme_value, artifact_value, check.get("rel_tol", REL_TOL)):
        return [f"MISMATCH\t{label}\treadme={readme_value} artifact={round(artifact_value, 4)}"]
    print(f"OK\t{label}\treadme={readme_value}\tartifact={round(artifact_value, 4)}")
    return problems


def main() -> int:
    if not README_PATH.exists():
        print(f"MISSING\tREADME.md not found at {README_PATH}")
        return 1
    readme = README_PATH.read_text()
    problems: list[str] = []
    for check in CHECKS:
        try:
            problems.extend(run_check(check, readme))
        except Exception as exc:  # artifact missing, path traversal failure, ...
            problems.append(f"MISSING\t{check['label']}\t{type(exc).__name__}: {exc}")

    print(f"\n{len(CHECKS) - len(problems)}/{len(CHECKS)} claim checks OK")
    for problem in problems:
        print(problem)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
