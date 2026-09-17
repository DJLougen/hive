"""DEPRECATED — replaced by scripts/hive_bench.py.

The previous version of this script did not run a real evaluation: the
agent loop was simulated and resolve outcomes were drawn from an RNG
(``resolved = rng.random() > 0.2``), so the reported 85% resolve rate and
0% baseline were constructed, not measured. It has been removed.

``scripts/hive_bench.py`` is the real benchmark: real repos with real
failing pytest suites, real tool execution, a real LLM over an
OpenAI-compatible endpoint, and a real pytest run as the resolve check.

This shim forwards its arguments to hive_bench so old commands/docs keep
working.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

print(
    "hive_swebench_eval.py was removed: it simulated the agent loop and drew\n"
    "resolve outcomes from an RNG — the numbers were not real.\n"
    "Forwarding to the real benchmark: scripts/hive_bench.py\n",
    file=sys.stderr,
)

from scripts.hive_bench import main

if __name__ == "__main__":
    sys.exit(main())
