#!/usr/bin/env bash
# run_spark_bench.sh — run the hive benchmark on the dev Spark and
# upload the JSON back to the hive repo via the GitHub REST API.
#
# Usage:
#   ./run_spark_bench.sh <github-token> [run-id]
#
# Requires:
#   - ssh djl@spark-d500 in ~/.ssh/config
#   - python3 on the Spark with hive, busybee-cpu, and honey-comb installed
#   - the hive repo cloned at ~/hive on the Spark
#   - GH_TOKEN with `contents:write` on DJLougen/hive
#
# Set SPARK_HOST to override the alias (default: djl@spark-d500).
# Add a ``Host spark-d500`` entry to ~/.ssh/config if you don't have one.
#
# What it does:
#   1. SSH to the Spark, cd ~/hive, run the macro + micro benchmarks.
#   2. scp the JSON files back to this machine.
#   3. POST each JSON to the hive repo as a single commit via the
#      GitHub contents API.
#
# The Spark numbers land under:
#   docs/benchmarks/spark-d500/<run-id>.json
# plus a latest.json pointer that the README links to.
set -euo pipefail

GITHUB_TOKEN="${1:?usage: $0 <github-token> [run-id]}"
RUN_ID="${2:-$(date -u +%Y%m%dT%H%M%SZ)}"
SPARK_HOST="${SPARK_HOST:-djl@spark-d500}"

REMOTE_DIR="~/hive"
LOCAL_TMP="$(mktemp -d)"
trap 'rm -rf "$LOCAL_TMP"' EXIT

echo "==> running macro benchmark on ${SPARK_HOST}"
ssh "$SPARK_HOST" "cd $REMOTE_DIR && \
    PYTHONPATH=. python3 scripts/hive_benchmark.py \
        --transcript-turns 200 --brain-writes 5000 \
        --honey-comb-mode auto --inference-backend echo \
        --output runs/spark-macro-${RUN_ID}.json \
        --runs 3 --quiet"
ssh "$SPARK_HOST" "cd $REMOTE_DIR && \
    PYTHONPATH=. python3 scripts/hive_benchmark_micro.py \
        --runs 5 --output runs/spark-micro-${RUN_ID}.json \
        --quiet"

echo "==> scp results back"
mkdir -p "$LOCAL_TMP/spark-d500"
scp "$SPARK_HOST:$REMOTE_DIR/runs/spark-macro-${RUN_ID}.json" "$LOCAL_TMP/spark-d500/macro.json"
scp "$SPARK_HOST:$REMOTE_DIR/runs/spark-micro-${RUN_ID}.json" "$LOCAL_TMP/spark-d500/micro.json"

echo "==> upload to hive repo via GitHub contents API"
REPO="DJLougen/hive"
BRANCH="main"
API="https://api.github.com/repos/$REPO/contents"

upload() {
    local path="$1"
    local local_file="$2"
    local b64
    b64=$(base64 -w0 < "$local_file")
    # Get current sha if exists (PUT update vs POST create)
    local sha=""
    sha=$(curl -fsSL \
        -H "Authorization: token $GITHUB_TOKEN" \
        "$API/$path?ref=$BRANCH" \
        | python3 -c "import json,sys; print(json.load(sys.stdin).get('sha',''))" 2>/dev/null || true)
    local body
    if [ -n "$sha" ]; then
        body=$(printf '{"message":"bench(spark-d500): %s","branch":"%s","sha":"%s","content":"%s"}' \
            "$path" "$BRANCH" "$sha" "$b64")
    else
        body=$(printf '{"message":"bench(spark-d500): %s","branch":"%s","content":"%s"}' \
            "$path" "$BRANCH" "$b64")
    fi
    curl -fsSL -X PUT \
        -H "Authorization: token $GITHUB_TOKEN" \
        -H "Content-Type: application/json" \
        -d "$body" \
        "$API/$path" \
        | python3 -c "import json,sys; d=json.load(sys.stdin); print('  uploaded', d.get('content',{}).get('path','?'))"
}

upload "docs/benchmarks/spark-d500/macro-${RUN_ID}.json" "$LOCAL_TMP/spark-d500/macro.json"
upload "docs/benchmarks/spark-d500/micro-${RUN_ID}.json" "$LOCAL_TMP/spark-d500/micro.json"

# Pointer file: latest.json is what the README links to
cp "$LOCAL_TMP/spark-d500/macro.json" "$LOCAL_TMP/spark-d500/latest-macro.json"
cp "$LOCAL_TMP/spark-d500/micro.json" "$LOCAL_TMP/spark-d500/latest-micro.json"
upload "docs/benchmarks/spark-d500/latest-macro.json" "$LOCAL_TMP/spark-d500/latest-macro.json"
upload "docs/benchmarks/spark-d500/latest-micro.json" "$LOCAL_TMP/spark-d500/latest-micro.json"

cat > "$LOCAL_TMP/spark-d500/manifest.json" <<EOF
{
  "run_id": "${RUN_ID}",
  "host": "${SPARK_HOST}",
  "machine": "DGX Spark",
  "date_utc": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "macro": "docs/benchmarks/spark-d500/macro-${RUN_ID}.json",
  "micro": "docs/benchmarks/spark-d500/micro-${RUN_ID}.json"
}
EOF
upload "docs/benchmarks/spark-d500/${RUN_ID}.json" "$LOCAL_TMP/spark-d500/manifest.json"

echo "==> done. Numbers are at:"
echo "    https://github.com/DJLougen/hive/blob/main/docs/benchmarks/spark-d500/latest-macro.json"
echo "    https://github.com/DJLougen/hive/blob/main/docs/benchmarks/spark-d500/latest-micro.json"
echo "    https://github.com/DJLougen/hive/blob/main/docs/benchmarks/spark-d500/${RUN_ID}.json"
