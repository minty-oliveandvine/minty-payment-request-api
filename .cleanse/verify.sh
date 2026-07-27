#!/bin/bash
# Re-run the suite and diff against the recorded baseline.
# Rule: no test that passes today may start failing.
# Exit 0 = no regressions. Exit 1 = a passing test started failing.
cd "$(dirname "$0")/.." || exit 2
LABEL="${1:-verify}"
OUT=".cleanse/current_failures.txt"

.cleanse-venv/bin/python -m pytest -p no:cacheprovider -q 2>&1 | tee .cleanse/last_run.txt \
  | grep -E '^(FAILED|ERROR) [a-zA-Z0-9_/]+\.py::' | sed 's/ - .*//' | sort -u > "$OUT"

echo "=== $LABEL ==="
tail -1 .cleanse/last_run.txt | grep -E 'passed|failed|error' || tail -3 .cleanse/last_run.txt

NEW=$(comm -13 .cleanse/baseline_failures.txt "$OUT")
FIXED=$(comm -23 .cleanse/baseline_failures.txt "$OUT")

if [ -n "$NEW" ]; then
  echo "!!! REGRESSION - these were passing and now fail:"
  echo "$NEW"
  exit 1
fi
[ -n "$FIXED" ] && { echo "note: no longer failing (unexpected, investigate):"; echo "$FIXED"; }
echo "OK: no regressions vs baseline ($(wc -l < .cleanse/baseline_failures.txt | tr -d ' ') known failures)"
exit 0
