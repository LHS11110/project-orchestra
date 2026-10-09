#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
[[ $# -ge 1 ]] || { echo 'Usage: run-load.sh <UserClass> [additional Locust options]' >&2; exit 2; }
profile="$1"; shift
[[ "$profile" =~ ^[A-Za-z][A-Za-z0-9]*User$ ]] || { echo "Choose a documented Locust User class." >&2; exit 2; }
mkdir -p results
# Reports are written to the dedicated Docker volume and exported as the host user.
[[ -w results ]] || { echo 'The report directory must be writable.' >&2; exit 1; }
stamp="$(date -u +%Y%m%dT%H%M%SZ)-$$"
set +e
docker compose run --rm --build load locust -f locustfile.py --headless \
    --users 5 --spawn-rate 1 --run-time 30s \
    --csv "/results/${profile}-${stamp}" --html "/results/${profile}-${stamp}.html" \
    "$@" "$profile"

status=$?
set -e
docker compose run --rm --no-deps --entrypoint tar load -C /results -cf - . | tar --no-same-owner -C results -xf -
exit "$status"
