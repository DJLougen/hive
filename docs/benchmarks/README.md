# Reference benchmark numbers (this folder)

Raw JSON reports from `scripts/hive_benchmark.py` and
`scripts/hive_benchmark_micro.py` runs on the dev hardware. The
README links to the `latest-*.json` files; older runs are kept here
for trend analysis.

## Files

* `latest-macro.json` — most recent `hive_benchmark.py` run, full
  envelope (mean ± stdev across `--runs`).
* `latest-micro.json` — most recent `hive_benchmark_micro.py` run,
  per-component rates.
* `<run-id>.json` — manifest for the run, including the host, the
  date, and the path to the macro/micro JSONs.
* `macro-<run-id>.json` / `micro-<run-id>.json` — per-run files;
  never overwritten.

## How to add your own

```bash
# Local machine
python scripts/hive_benchmark.py \
    --transcript-turns 200 --brain-writes 5000 \
    --honey-comb-mode auto --inference-backend echo \
    --runs 3 --output docs/benchmarks/<your-host>/latest-macro.json
```

For the DGX Spark specifically, `scripts/run_spark_bench.sh` does the
ssh + scp + GitHub upload in one step:

```bash
./scripts/run_spark_bench.sh $GITHUB_TOKEN
```
