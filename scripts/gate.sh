#!/usr/bin/env bash
exec bash -c "$(tr -d '\r' < "$0" | tail -n +3)" "$0" "$@" # Accept Windows patch newlines.
set -euo pipefail
cd "$(dirname "$0")/.."
tag=${1:?Usage: bash scripts/gate.sh drep-N}
case "$tag" in
  *[!a-zA-Z0-9_-]*|'') echo 'Use a simple test directory tag' >&2; exit 2 ;;
esac
root="${GATE_ROOT:-C:/seldon/tests/${tag}-1}"
mkdir -p "$root/temp"
export PYTHONPATH='src;tests/seldon'
export TEMP="$root/temp" TMP="$root/temp"
export PYTHONDONTWRITEBYTECODE=1
# Parallel journeys (16 cores; Oren 11-14 "gates keep slowing us down"): the floor is the longest single journey.
python -B -m pytest tests/seldon -o 'python_files=test_*.py size_*_journey.py' -q \
  -p no:cacheprovider -n "${GATE_WORKERS:-8}" --dist load --basetemp="$root/pytest"
