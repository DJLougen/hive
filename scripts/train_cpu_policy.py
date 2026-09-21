"""Train the CPU routing policy from logged agent trajectories.

Collect trajectories with ``hive_bench.py --log runs.jsonl`` (every turn's
``state -> action`` is logged, LLM and policy decisions alike — the CPU model
learns to imitate whichever source produced the decision). Then:

    python scripts/train_cpu_policy.py --trajectories runs.jsonl \
        --out /tmp/cpu_router.joblib

The saved policy drops into the bench via
``hive_bench.py --policy trained --policy-path benchmarks/cpu_router.joblib``
or anywhere a ``predict(state)`` policy is accepted
(``HiveStack(busybee_policy=...)``). ``hive.model_registry.sign_model()`` can
sign the .joblib for untrusted-model loading.

Held-out hygiene (fail-closed):

* ``--exclude-suite <manifest>`` drops every trajectory row whose ``task``
  belongs to the named suite — use it to keep an eval tier out of training.
* ``--eval-suite <manifest>`` declares the suite you intend to score the
  policy on; if any corpus row carries one of those task ids the script
  refuses to train unless ``--allow-eval-overlap`` is passed explicitly.
* Rows with no ``task`` id are an error whenever either option is used —
  an untagged row cannot be proven disjoint from a held-out suite.
* An empty corpus (no usable rows) is an error, not a silently trained
  degenerate model.
* ``--out`` never overwrites an existing file without ``--overwrite`` —
  the committed ``benchmarks/cpu_router.joblib`` cannot be clobbered by
  accident.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def _load_suite_task_ids(manifest: Path) -> set[str]:
    """Read a suite manifest (``suite*.json``) and return its task ids."""
    if not manifest.exists():
        raise FileNotFoundError(f"suite manifest {manifest} does not exist")
    suite = json.loads(manifest.read_text())
    tasks = suite.get("tasks")
    if not isinstance(tasks, list) or not all(isinstance(t, str) for t in tasks):
        raise ValueError(f"{manifest}: expected a manifest with a 'tasks' list of ids")
    return set(tasks)


def _read_rows(path: Path) -> list[dict]:
    rows: list[dict] = []
    for lineno, line in enumerate(path.read_text().splitlines(), start=1):
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{lineno}: invalid JSONL row: {exc}") from exc
    return rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--trajectories", required=True, help="JSONL from hive_bench --log")
    ap.add_argument("--out", required=True, help="output .joblib path")
    ap.add_argument("--threshold", type=float, default=0.55,
                    help="escalation floor on predicted-class probability")
    ap.add_argument("--exclude-suite", default=None, metavar="MANIFEST",
                    help="suite manifest whose task ids are dropped from the "
                         "training corpus before fitting")
    ap.add_argument("--eval-suite", default=None, metavar="MANIFEST",
                    help="suite manifest the trained policy will be scored on; "
                         "training fails if any corpus row carries one of its "
                         "task ids (held-out guard)")
    ap.add_argument("--allow-eval-overlap", action="store_true",
                    help="explicitly permit corpus rows from --eval-suite tasks "
                         "(train-on-test; only for diagnostics, never for a "
                         "published held-out claim)")
    ap.add_argument("--overwrite", action="store_true",
                    help="permit replacing an existing --out file (required to "
                         "update a committed checkpoint)")
    args = ap.parse_args(argv)

    out = Path(args.out)
    if out.exists() and not args.overwrite:
        print(f"error: {out} already exists — pass --overwrite to replace it "
              f"(committed checkpoints are not overwritten by default)",
              file=sys.stderr)
        return 2

    traj = Path(args.trajectories)
    if not traj.exists():
        print(f"error: trajectories file {traj} does not exist", file=sys.stderr)
        return 2
    try:
        rows = _read_rows(traj)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if not rows:
        print(f"error: {traj} contains no rows — empty corpus", file=sys.stderr)
        return 2

    exclude_ids: set[str] = set()
    if args.exclude_suite:
        try:
            exclude_ids = _load_suite_task_ids(Path(args.exclude_suite))
        except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
            print(f"error: --exclude-suite: {exc}", file=sys.stderr)
            return 2
        if not exclude_ids:
            print(f"error: --exclude-suite {args.exclude_suite} names no tasks",
                  file=sys.stderr)
            return 2

    eval_ids: set[str] = set()
    if args.eval_suite:
        try:
            eval_ids = _load_suite_task_ids(Path(args.eval_suite))
        except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
            print(f"error: --eval-suite: {exc}", file=sys.stderr)
            return 2
        if not eval_ids:
            print(f"error: --eval-suite {args.eval_suite} names no tasks",
                  file=sys.stderr)
            return 2

    if exclude_ids or eval_ids:
        untagged = sum(1 for r in rows if not r.get("task"))
        if untagged:
            print(f"error: {untagged} row(s) carry no task id — cannot prove "
                  f"the corpus is disjoint from the excluded/eval suite",
                  file=sys.stderr)
            return 2

    if exclude_ids:
        before = len(rows)
        rows = [r for r in rows if r["task"] not in exclude_ids]
        print(f"excluded {before - len(rows)} rows from suite "
              f"{args.exclude_suite}")
        if not rows:
            print("error: exclusion emptied the corpus — nothing to train on",
                  file=sys.stderr)
            return 2

    if eval_ids and not args.allow_eval_overlap:
        overlap = sorted({r["task"] for r in rows} & eval_ids)
        if overlap:
            print(f"error: corpus contains {len(overlap)} task(s) from the "
                  f"declared eval suite: {overlap} — retrain with "
                  f"--exclude-suite {args.eval_suite} or pass "
                  f"--allow-eval-overlap to acknowledge train-on-test",
                  file=sys.stderr)
            return 2

    from hive.cpu_policy import CPURouterPolicy

    policy = CPURouterPolicy(threshold=args.threshold)
    try:
        metrics = policy.fit(rows)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    out.parent.mkdir(parents=True, exist_ok=True)
    policy.save(out)
    print(f"trained on {metrics['samples']} decisions, classes={metrics['class_counts']}")
    print(f"saved -> {out}")
    if not out.with_suffix(".joblib.sig").exists():
        print("note: model is unsigned — CPURouterPolicy.load will refuse it "
              "unless HIVE_ALLOW_UNSIGNED_MODEL=1 or it is signed with "
              "hive.model_registry.ModelRegistry.sign_model()")
    return 0


if __name__ == "__main__":
    sys.exit(main())
