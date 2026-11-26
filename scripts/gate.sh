#!/usr/bin/env bash
exec bash -c "$(tr -d '\r' < "$0" | tail -n +3)" "$0" "$@" # Accept Windows patch newlines.
set -euo pipefail
cd "$(dirname "$0")/.."
tag=${1:?Usage: bash scripts/gate.sh drep-N [--all]}
case "$tag" in
  *[!a-zA-Z0-9_-]*|'') echo 'Use a simple test directory tag' >&2; exit 2 ;;
esac
root="${GATE_ROOT:-C:/seldon/tests/${tag}-1}"
mkdir -p "$root/temp"
export PYTHONPATH='src;tests/seldon'
export TEMP="$root/temp" TMP="$root/temp"
export PYTHONDONTWRITEBYTECODE=1
# Landing gate = fresh Lead + the shared pieces it uses (scripts/gate-keep.txt).
# --all runs the full archive suite (old Lead + paused Orchestrator) on demand.
args=() all=
for a in "${@:2}"; do [[ $a == --all ]] && all=1 || args+=("$a"); done
if [[ -n $all ]]; then
  targets=(tests/seldon)
else
  mapfile -t targets < <(tr -d '\r' < scripts/gate-keep.txt | grep -v '^#' | grep .)
  targets+=(tests/seldon/test_lead2_*.py)
  while read -r t; do targets+=("tests/seldon/test_journey.py::$t"); done \
    < <(grep -oE '^def test_[a-z0-9_]*fresh_lead[a-z0-9_]*' tests/seldon/test_journey.py | cut -c5-)
fi
# Parallel journeys: the floor is the longest single journey.
python -B -m pytest "${targets[@]}" -o 'python_files=test_*.py size_*_journey.py' -q \
  -p no:cacheprovider -n "${GATE_WORKERS:-8}" --dist load --basetemp="$root/pytest" ${args[@]+"${args[@]}"}
