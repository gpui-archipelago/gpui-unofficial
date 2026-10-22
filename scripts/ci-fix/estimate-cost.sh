#!/usr/bin/env bash
# Thin wrapper around cost.py so the whole workflow stays in one directory.
#
# Examples:
#   scripts/ci-fix/estimate-cost.sh --scenarios
#   scripts/ci-fix/estimate-cost.sh --input 180000 --output 12000
#   scripts/ci-fix/estimate-cost.sh --usage target/ci-fix/<run>/usage.json
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$SCRIPT_DIR/cost.py" "$@"
