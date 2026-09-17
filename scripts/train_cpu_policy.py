"""Train the CPU routing policy from logged agent trajectories.

Collect trajectories with ``hive_bench.py --log runs.jsonl`` (every turn's
``state -> action`` is logged, LLM and policy decisions alike — the CPU model
learns to imitate whichever source produced the decision). Then:

    python scripts/train_cpu_policy.py --trajectories runs.jsonl \
        --out benchmarks/cpu_router.joblib

The saved policy drops into the bench via
``hive_bench.py --policy trained --policy-path benchmarks/cpu_router.joblib``
or anywhere a ``predict(state)`` policy is accepted
(``HiveStack(busybee_policy=...)``). ``hive.model_registry.sign_model()`` can
sign the .joblib for untrusted-model loading.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--trajectories", required=True, help="JSONL from hive_bench --log")
    ap.add_argument("--out", required=True, help="output .joblib path")
    ap.add_argument("--threshold", type=float, default=0.55,
                    help="escalation floor on predicted-class probability")
    args = ap.parse_args()

    from hive.cpu_policy import CPURouterPolicy

    policy = CPURouterPolicy(threshold=args.threshold)
    metrics = policy.fit_file(args.trajectories)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    policy.save(out)
    print(f"trained on {metrics['samples']} decisions, classes={metrics['class_counts']}")
    print(f"saved -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
