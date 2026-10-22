#!/usr/bin/env bash
# Verify a candidate fix with the cheapest checks that still cover the change.
#
# Always: xtask unit tests + script syntax checks.
# If xtask transform/publish/isolated code changed: reproduce against a tag.
# If workflow YAML changed: actionlint (or a Python YAML parse fallback).
#
# Examples:
#   scripts/ci-fix/verify.sh --run 39684649841
#   scripts/ci-fix/verify.sh --run 39684649841 --target gpui-wgpu
#   scripts/ci-fix/verify.sh --tag v1.25.2 --full
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
source "$SCRIPT_DIR/lib.sh"

usage() {
  cat <<'EOF'
Usage: verify.sh [options]

  --run ID              Failure run id (provides the tag for reproduction)
  --tag TAG             Zed tag for reproduction
  --target NAME         Crate to reproduce; repeatable
  --full                Run the full host crate matrix + isolated packaging
  --changed-from REF    Git ref to diff against (default: origin/main)
  --skip-reproduce      Only run the in-repo checks (tests, syntax, YAML)
  -h, --help            Show this help
EOF
}

RUN=""
TAG=""
TARGETS=()
FULL=0
CHANGED_FROM="${CI_FIX_BASE:-origin/main}"
SKIP_REPRODUCE=0

while [ $# -gt 0 ]; do
  case "$1" in
    --run) RUN="${2:?--run needs an id}"; shift 2 ;;
    --tag) TAG="${2:?--tag needs a value}"; shift 2 ;;
    --target) TARGETS+=("${2:?--target needs a value}"); shift 2 ;;
    --full) FULL=1; shift ;;
    --changed-from) CHANGED_FROM="${2:?--changed-from needs a ref}"; shift 2 ;;
    --skip-reproduce) SKIP_REPRODUCE=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown argument: $1 (try --help)" ;;
  esac
done

require_cmd cargo git python3 jq
cargo_env

if [ -z "$TAG" ] && [ -n "$RUN" ]; then
  meta="$CACHE_DIR/$RUN/meta.json"
  if [ -f "$meta" ]; then
    TAG="$(jq -r '.zed_tag // empty' "$meta" 2>/dev/null || true)"
  fi
  [ "$TAG" = "unknown" ] && TAG=""
fi

# ---------------------------------------------------------------------------
# Which files changed?
# ---------------------------------------------------------------------------

if ! git -C "$REPO_ROOT" rev-parse --verify --quiet "$CHANGED_FROM" >/dev/null; then
  if git -C "$REPO_ROOT" rev-parse --verify --quiet main >/dev/null; then
    CHANGED_FROM="main"
  else
    CHANGED_FROM="HEAD"
  fi
fi

changed="$(
  {
    git -C "$REPO_ROOT" diff --name-only "$CHANGED_FROM"...HEAD 2>/dev/null || true
    git -C "$REPO_ROOT" diff --name-only 2>/dev/null || true
    git -C "$REPO_ROOT" diff --cached --name-only 2>/dev/null || true
    git -C "$REPO_ROOT" ls-files --others --exclude-standard 2>/dev/null || true
  } | sort -u
)"

needs_transform=0
needs_workflow=0
needs_scripts=0
if printf '%s\n' "$changed" | grep -qE '(^|/)xtask/src/(transform|publish|test_isolated|verify)\.rs$'; then
  needs_transform=1
fi
if printf '%s\n' "$changed" | grep -qE '(^|/)\.github/workflows/.*\.ya?ml$'; then
  needs_workflow=1
fi
if printf '%s\n' "$changed" | grep -qE '(^|/)scripts/ci-fix/'; then
  needs_scripts=1
fi

if [ -z "$changed" ]; then
  info "No changed files detected against $CHANGED_FROM"
fi

# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------

declare -a check_names=()
declare -a check_results=()

record() {
  check_names+=("$1")
  check_results+=("$2")
}

failures=0

info "cargo test -p xtask"
if ( cd "$REPO_ROOT" && cargo test --quiet -p xtask ); then
  record "xtask unit tests" "pass"
else
  record "xtask unit tests" "FAIL"
  failures=$((failures + 1))
fi

info "cargo build -p xtask"
if ( cd "$REPO_ROOT" && cargo build --quiet -p xtask ); then
  record "xtask build" "pass"
else
  record "xtask build" "FAIL"
  failures=$((failures + 1))
fi

if [ "$needs_scripts" -eq 1 ]; then
  info "syntax-checking helper scripts"
  scripts_ok=1
  for f in "$SCRIPT_DIR"/*.sh; do
    if ! bash -n "$f"; then
      err "bash syntax error: $f"
      scripts_ok=0
    fi
  done
  if ! python3 -c 'import sys; [compile(open(f, encoding="utf-8").read(), f, "exec") for f in sys.argv[1:]]' "$SCRIPT_DIR"/*.py; then
    err "python syntax error in scripts/ci-fix"
    scripts_ok=0
  fi
  if [ "$scripts_ok" -eq 1 ]; then
    record "helper script syntax" "pass"
  else
    record "helper script syntax" "FAIL"
    failures=$((failures + 1))
  fi

  # Cheap smoke tests: every entry point must at least print help.
  smoke_ok=1
  for probe in \
    "$SCRIPT_DIR/collect.sh --help" \
    "$SCRIPT_DIR/reproduce.sh --help" \
    "$SCRIPT_DIR/verify.sh --help" \
    "$SCRIPT_DIR/submit.sh --help" \
    "$SCRIPT_DIR/estimate-cost.sh --help" \
    "python3 $SCRIPT_DIR/deepseek_agent.py --help"; do
    if ! sh -c "$probe" >/dev/null 2>&1; then
      err "smoke test failed: $probe"
      smoke_ok=0
    fi
  done
  if [ "$smoke_ok" -eq 1 ]; then
    record "script smoke tests" "pass"
  else
    record "script smoke tests" "FAIL"
    failures=$((failures + 1))
  fi
fi

if [ "$needs_workflow" -eq 1 ]; then
  info "validating workflow YAML"
  workflow_ok=1
  if command -v actionlint >/dev/null 2>&1; then
    if ! ( cd "$REPO_ROOT" && actionlint .github/workflows/*.yml ); then
      workflow_ok=0
    fi
  elif python3 -c 'import yaml' >/dev/null 2>&1; then
    if ! python3 -c 'import sys, yaml; [yaml.safe_load(open(f, encoding="utf-8")) for f in sys.argv[1:]]' "$REPO_ROOT"/.github/workflows/*.yml; then
      workflow_ok=0
    fi
  else
    warn "neither actionlint nor PyYAML is available; skipping YAML validation"
  fi
  if [ "$workflow_ok" -eq 1 ]; then
    record "workflow YAML" "pass"
  else
    record "workflow YAML" "FAIL"
    failures=$((failures + 1))
  fi
fi

if [ "$needs_transform" -eq 1 ]; then
  if [ "$SKIP_REPRODUCE" -eq 1 ]; then
    record "reproduce" "skipped"
  elif [ -z "$TAG" ]; then
    err "xtask transform/publish code changed, but no --tag/--run was supplied"
    record "reproduce" "FAIL (no tag)"
    failures=$((failures + 1))
  else
    info "reproducing against $TAG"
    reproduce_args=(--tag "$TAG")
    for t in "${TARGETS[@]:-}"; do
      [ -n "$t" ] && reproduce_args+=(--target "$t")
    done
    if [ "$FULL" -eq 1 ]; then
      reproduce_args+=(--all --isolated --examples)
    fi
    if "$SCRIPT_DIR/reproduce.sh" "${reproduce_args[@]}"; then
      record "reproduce ($TAG)" "pass"
    else
      record "reproduce ($TAG)" "FAIL"
      failures=$((failures + 1))
    fi
  fi
elif [ "$FULL" -eq 1 ] && [ -n "$TAG" ]; then
  info "full reproduction against $TAG"
  full_args=(--tag "$TAG" --all --isolated --examples)
  for t in "${TARGETS[@]:-}"; do
    [ -n "$t" ] && full_args+=(--target "$t")
  done
  if "$SCRIPT_DIR/reproduce.sh" "${full_args[@]}"; then
    record "full reproduce ($TAG)" "pass"
  else
    record "full reproduce ($TAG)" "FAIL"
    failures=$((failures + 1))
  fi
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

printf '\n%s--- verification summary ---%s\n' "$C_INFO" "$C_RESET"
i=0
while [ "$i" -lt "${#check_names[@]}" ]; do
  printf '  %-32s %s\n' "${check_names[$i]}" "${check_results[$i]}"
  i=$((i + 1))
done

if [ "$failures" -ne 0 ]; then
  err "$failures check(s) failed"
  exit 1
fi

ok "all checks passed"
