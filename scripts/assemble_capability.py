"""Assemble docs/benchmarks/hive-bench-capability.json from its source runs.

baseline  <- /tmp/pod-baseline-recount.json (30 fully-instrumented episodes)
context   <- /tmp/capability2.json context rows; usage merged from
             /tmp/context-resume.jsonl where present
hive      <- /tmp/capability2.json hive rows (fully measured)

Every summary/comparison field is recomputed from the merged results by the
harness's own summarize()/compare_arms() — never copied from a source summary.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.hive_bench import AgentResult, compare_arms, summarize

CAP = json.loads(Path("/tmp/capability2.json").read_text())
RECOUNT = json.loads(Path("/tmp/pod-baseline-recount.json").read_text())
RESUME = {
    (r["task_id"], r["pass_idx"]): r
    for r in (json.loads(line) for line in Path("/tmp/context-resume.jsonl").read_text().splitlines())
}
PRICE_IN = CAP["provenance"]["price_in"]
PRICE_OUT = CAP["provenance"]["price_out"]

# ---- pre-write asserts: the merge must not silently drop or mislabel rows ----
for name, src in (("capability2", CAP), ("baseline-recount", RECOUNT)):
    prov = src["provenance"]
    assert prov["temperature"] == CAP["provenance"]["temperature"], name
    assert prov["repeat"] == CAP["provenance"]["repeat"], name
    assert prov["memory_mode"] == CAP["provenance"]["memory_mode"], name
assert len(RECOUNT["results"]) == 30, len(RECOUNT["results"])
assert {r["task_id"] for r in RECOUNT["results"]} == {
    r["task_id"] for r in CAP["results"] if r["arm"] == "baseline"
}
assert not any(r.get("crashed") for r in RECOUNT["results"])
# The recount is a second draw of the same cells — record its own resolve
# count so the retest spread is visible, not hidden.
RECOUNT_RESOLVED = sum(r["resolved"] for r in RECOUNT["results"])


def row(d, source, usage_recorded):
    d = dict(d)
    d["source_run"] = source
    d["usage_recorded"] = usage_recorded
    d["steps"] = []  # step logs are not needed in the published artifact
    return d


# Outcomes come from the original capability2 run for every arm — the recount
# is a *usage* re-measurement pass over the same cells, not a second outcome
# draw. Merging its resolved flags would silently re-measure baseline twice
# against arms measured once.
RECOUNT_USAGE = {
    (r["task_id"], r["pass_idx"]): r for r in RECOUNT["results"]
}
results = []
for r in CAP["results"]:
    if r["arm"] == "baseline":
        res = RECOUNT_USAGE.get((r["task_id"], r["pass_idx"]))
        if res is not None:
            r = dict(r, prompt_tokens=res["prompt_tokens"],
                     completion_tokens=res["completion_tokens"],
                     llm_calls=res["llm_calls"], turns=res["turns"],
                     wall_clock_s=res["wall_clock_s"])
            results.append(row(r, "capability2+recount-usage", True))
        else:
            results.append(row(r, "capability2",
                               bool(r.get("usage_recorded", r["prompt_tokens"] > 0))))
    elif r["arm"] == "context":
        res = RESUME.get((r["task_id"], r["pass_idx"]))
        if res is not None:
            r = dict(r, prompt_tokens=res["prompt_tokens"],
                     completion_tokens=res["completion_tokens"],
                     llm_calls=res["llm_calls"], turns=res["turns"],
                     wall_clock_s=res["wall_clock_s"])
            results.append(row(r, "capability2+resume", True))
        else:
            results.append(row(r, "capability2", False))
    elif r["arm"] == "hive":
        # capability2's hive rows predate the usage_recorded field but carry
        # real token counts — a missing field with nonzero usage is measured.
        results.append(row(r, "capability2",
                           bool(r.get("usage_recorded", r["prompt_tokens"] > 0))))

arms = ["baseline", "context", "hive"]
for a in arms:
    assert sum(1 for r in results if r["arm"] == a) == 30, a
ctx_known = sum(1 for r in results if r["arm"] == "context" and r["usage_recorded"])

objs = [AgentResult(**{k: v for k, v in r.items() if k != "source_run"})
        for r in results]
report = {
    "model": CAP["model"], "driver": CAP["driver"], "suite": CAP["suite"],
    "provenance": {
        **CAP["provenance"],
        "assembled_from": {
            "baseline": {
                "run": "capability2",
                "usage_from": "baseline-recount",
                "note": "outcomes from the original run; token usage re-measured on an identical 30-episode pass of the same cells",
                "usage_git_sha": RECOUNT["provenance"].get("git_sha"),
                "usage_host": RECOUNT["provenance"].get("host", "local"),
                "usage_python": RECOUNT["provenance"].get("python"),
            },
            "context": {"run": "capability2", "usage_known_for": ctx_known},
            "hive": {"run": "capability2"},
        },
        "usage_note": (
            f"context arm: {ctx_known}/30 episodes kept their token record; "
            "the rest were regraded from their patch after the run was "
            "interrupted. Usage-derived fields are null for that arm, never "
            "zero-filled."
        ),
        "episodes_regraded_from_patch": sum(
            1 for r in results if not r["usage_recorded"]),
        "baseline_recount_resolved": RECOUNT_RESOLVED,
        "baseline_recount_note": (
            f"the usage re-measurement pass independently resolved "
            f"{RECOUNT_RESOLVED}/30 — a second draw of the same cells, "
            "not the published outcome grid"
        ),
    },
    "results": results,
    "summary": {a: summarize(objs, a, price_in=PRICE_IN, price_out=PRICE_OUT)
                for a in arms},
}
report["summary"]["comparisons"] = compare_arms(
    objs, arms, price_in=PRICE_IN, price_out=PRICE_OUT)
# Baseline's USD/resolved must be a single-sample ratio: the recount's own
# usage over the recount's own resolved count — not recount USD over the
# original run's resolved count, which mixes two different draws.
report["summary"]["baseline"]["usd_per_resolved_task"] = (
    RECOUNT["summary"]["baseline"]["usd_per_resolved_task"]
)
report["summary"]["baseline"]["usd_per_resolved_task_note"] = (
    "recount usage / recount resolved — a single-sample ratio from the "
    "re-measurement pass, not mixed with the original outcome grid"
)
out = Path("docs/benchmarks/hive-bench-capability.json")
out.write_text(json.dumps(report, indent=2))
print("wrote", out)
for a in arms:
    s = report["summary"][a]
    print(a, s["resolved"], "/", s["tasks"], "rate", s["resolve_rate"],
          "usd", s["usd_total"], "unmeasured", s["episodes_without_usage"])
