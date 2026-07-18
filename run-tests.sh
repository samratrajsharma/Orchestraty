#!/usr/bin/env bash
# run-tests.sh - run the Orchestraty unit test suite. Prereq: pip install -r requirements-dev.txt
fail=0
for c in orchestrator agents model_server; do
  echo ""; echo "== $c =="
  ( cd "$c" && PYTHONPATH=. pytest tests/ -q ) || fail=1
done
echo ""
if [ "$fail" -ne 0 ]; then echo "TESTS FAILED"; exit 1; fi
echo "ALL UNIT TESTS PASSED"
