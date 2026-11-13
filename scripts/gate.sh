#!/usr/bin/env bash
exec bash -c "$(tr -d '\r' < "$0" | tail -n +3)" "$0" "$@" # Accept Windows patch newlines.
set -euo pipefail
cd "$(dirname "$0")/.."
tag=${1:?Usage: bash scripts/gate.sh drep-N}
case "$tag" in
  *[!a-zA-Z0-9_-]*|'') echo 'Use a simple test directory tag' >&2; exit 2 ;;
esac
root="C:/seldon/tests/$tag"
mkdir -p "$root/temp"
export PYTHONPATH='src;tests/seldon'
export TEMP="$root/temp" TMP="$root/temp"
export PYTHONDONTWRITEBYTECODE=1
python -B -m pytest tests/seldon -q -p no:cacheprovider --basetemp="$root/pytest"
