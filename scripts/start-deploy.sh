#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec "${ORCHESTRA_PYTHON:-python3}" "$ROOT/scripts/start-stack.py" production "$@"
