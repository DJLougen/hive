"""DEPRECATED — folded into scripts/hive_bench.py.

This script measured "resolve rate" by drawing outcomes from an RNG
(``resolved = rng.random() > 0.2``) over simulated transcripts — the numbers
were not real. Compression behaviour on real workloads is now recorded by
``scripts/hive_bench.py`` (per-step ``observation_bytes`` vs ``context_bytes``
in the JSON report), and long-context compression evidence lives in
``scripts/hive_long_context_eval.py``.
"""

from __future__ import annotations

import sys


def main() -> int:
    print(
        "hive_compression_sweep.py was removed: its resolve numbers came from an\n"
        "RNG, not real task outcomes.\n"
        "- Real A/B + compression stats:  python scripts/hive_bench.py --help\n"
        "- Long-context compression:      python scripts/hive_long_context_eval.py --smoke\n",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
