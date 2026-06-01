# Benchmark numbers

Raw JSON reports from `scripts/hive_benchmark.py` and
`scripts/hive_benchmark_micro.py` runs on real hardware. The README
device matrix links to the `latest-*.json` files; older runs are
kept here for trend analysis.

## Layout

```
docs/benchmarks/
├── README.md                    ← this file
├── latest-macro.json            ← most recent macro envelope
├── latest-micro.json            ← most recent per-component micro envelope
└── spark-d500/                  ← per-host folder
    ├── 20260601T145528Z.json    ← manifest (host, date, paths)
    ├── macro-20260601T145528Z.json
    └── micro-20260601T145528Z.json
```

`latest-macro.json` / `latest-micro.json` are the canonical pointers
the README links to. Per-run files are never overwritten.

## How to add your own machine

1. Run the benchmark on the host:

   ```bash
   python scripts/hive_benchmark.py \
       --transcript-turns 200 --brain-writes 5000 \
       --honey-comb-mode auto --inference-backend echo \
       --runs 3 --output docs/benchmarks/<your-host>/latest-macro.json
   ```

2. Open a PR that adds the JSON and updates the device matrix in the
   README with your row. Use the `performance` issue template first if
   you want to discuss the numbers before committing them.

## DGX Spark (spark-d500)

The first validated aarch64 row in the README device matrix comes from
`djl@spark-d500`. The manifest at
[`spark-d500/20260601T145528Z.json`](spark-d500/20260601T145528Z.json)
captures host, date, and pointers to the macro / micro envelopes.

Real numbers from that run (mean across 3 runs unless noted):

| Component | Rate | Hardware | Notes |
|---|---:|---|---|
| busyBee (no policy) | 1.73 M r/s | GB10 aarch64 | macro |
| busyBee (with policy) | 112 ± 0 r/s | GB10 aarch64 | micro, 5 runs |
| honey-comb `rule_fast` | 19.8 k msg/s | GB10 aarch64 | macro |
| honey-comb ML classifier | 1.18 k msg/s | GB10 aarch64 | macro, 17× slower on ARM64 |
| rust-brain | 135 k w/s | GB10 aarch64 | macro |
| rust-brain | 176.8 ± 7.0 k w/s | GB10 aarch64 | micro, 5 runs |
| peak RSS | 652.6 MB | GB10 aarch64 | mean over 3 runs |
| CPU energy (est.) | 3.47 J | GB10 aarch64 | wall × 0.4 TDP |
| GPU energy (NVML) | 0.29 J | GB10 aarch64 | inference is echo; CUDA path unused |
| peak GPU | 0 MB | GB10 aarch64 | same — no CUDA workload hit residency |

To re-run on the Spark:

```bash
./scripts/run_spark_bench.sh $GITHUB_TOKEN
```

The script ssh's to `djl@spark-d500` (override with `SPARK_HOST=...`),
runs macro + micro with `--runs 3` / `--runs 5`, scp's the JSONs back,
and uploads them to this folder via the GitHub contents API.
