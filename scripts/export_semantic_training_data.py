"""Export the semantic comparison corpus for d-Jeff training / evaluation.

Reads JSONL written by :class:`hive.semantic_records.JsonlRecordSink` (one record
per semantic decision) and emits a trainer-friendly dataset, keeping the two
supervision signals **separate**:

* ``teacher`` — the full backend distribution (Jev today). A soft target.
* ``label``  — the action the agent actually took and its outcome, where known.
               A hard target, but only on the subset where the episode resolved.

The plan is explicit that Jev is a teacher, not truth
("Jev output is not automatically ground truth"). This script therefore never
merges the two into a single ``target`` field, and records the provenance of
each.

Splitting is **group-wise by episode** (``group_id``), so decisions from one
agent episode never straddle train/validation/test.

Usage::

    python scripts/export_semantic_training_data.py \
        --records docs/benchmarks/semantic-records.jsonl \
        --out /tmp/djeff-corpus.jsonl \
        --frozen-eval docs/benchmarks/djeff-eval-v1.jsonl
"""

from __future__ import annotations

import argparse
import json
import random
from collections import defaultdict
from pathlib import Path


def load_records(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def to_example(rec: dict) -> dict:
    """One training example: state, teacher distribution, optional hard label."""
    prediction = rec.get("prediction") or {}
    tool = (prediction or {}).get("tool") or {}
    return {
        "group_id": rec.get("group_id"),
        "schema_version": rec.get("schema_version"),
        "state": rec.get("state"),
        "questions": rec.get("questions"),
        "teacher": {
            "backend": rec.get("backend"),
            "model_revision": rec.get("model_revision"),
            "tool_distribution": tool.get("probabilities"),
            "safe_to_execute": (prediction.get("safe_to_execute") or {}).get("noul"),
            "requires_reasoning": (prediction.get("requires_reasoning") or {}).get("noul"),
            "needs_llm": (prediction.get("needs_llm") or {}).get("noul"),
        },
        "label": {
            "actual_action": rec.get("actual_action"),
            "outcome": rec.get("outcome"),
        },
        "accepted_by_hive": rec.get("accepted_by_hive"),
        "target_provenance": rec.get("target_provenance"),
    }


def group_split(groups: list[str], *, seed: int = 0, val_frac: float = 0.15,
                test_frac: float = 0.15) -> dict[str, set[str]]:
    """Split whole groups (episodes) into train/val/test — never within a group."""
    g = sorted(groups)
    random.Random(seed).shuffle(g)
    n = len(g)
    n_test = round(n * test_frac)
    n_val = round(n * val_frac)
    return {
        "test": set(g[:n_test]),
        "val": set(g[n_test:n_test + n_val]),
        "train": set(g[n_test + n_val:]),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--records", required=True, help="JSONL from a semantic record sink")
    ap.add_argument("--out", required=True, help="output corpus JSONL")
    ap.add_argument("--frozen-eval", default=None,
                    help="also write a frozen eval slice (never trained on)")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--val-frac", type=float, default=0.15)
    ap.add_argument("--test-frac", type=float, default=0.15)
    args = ap.parse_args()

    records = load_records(Path(args.records))
    if not records:
        raise SystemExit(f"no records in {args.records}")

    # Group by episode; ungrouped records become their own singleton group so
    # they can never leak across a split.
    by_group: dict[str, list[dict]] = defaultdict(list)
    for i, rec in enumerate(records):
        by_group[rec.get("group_id") or f"ungrouped-{i}"].append(rec)

    splits = group_split(list(by_group), seed=args.seed,
                         val_frac=args.val_frac, test_frac=args.test_frac)

    out_chunks: list[str] = []
    counts = dict.fromkeys(("train", "val", "test"), 0)
    for split, groups in splits.items():
        for gid in sorted(groups):
            for rec in by_group[gid]:
                example = to_example(rec)
                example["split"] = split
                out_chunks.append(json.dumps(example, default=str))
                counts[split] += 1

    Path(args.out).write_text("\n".join(out_chunks) + "\n", encoding="utf-8")
    print(f"wrote {sum(counts.values())} examples -> {args.out}")
    print(f"  groups={len(by_group)}  split={counts}")
    labeled = sum(1 for r in records if r.get("actual_action"))
    print(f"  with a hard actual_action label: {labeled}/{len(records)}")

    if args.frozen_eval:
        # Freeze the test split as an eval set that must never be trained on.
        frozen = [c for c in out_chunks if json.loads(c)["split"] == "test"]
        Path(args.frozen_eval).write_text("\n".join(frozen) + "\n", encoding="utf-8")
        print(f"froze {len(frozen)} examples -> {args.frozen_eval} (do not train on these)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
