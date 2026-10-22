#!/usr/bin/env bash
# Reproduce a release/CI failure locally with the smallest useful build.
#
# Default behaviour: shallow-clone (or reuse) the zed tag, build xtask, run the
# transform with path dependencies, and `cargo check` one crate. That is usually
# enough to confirm a transform bug without paying for the full cross-platform
# matrix.
#
# Examples:
#   scripts/ci-fix/reproduce.sh --tag v1.25.2 --target gpui-wgpu
#   scripts/ci-fix/reproduce.sh --run 39684649841          # tag from collect.sh
#   scripts/ci-fix/reproduce.sh --tag v1.25.2 --all        # every host crate
#   scripts/ci-fix/reproduce.sh --tag v1.25.2 --target gpui --isolated
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
source "$SCRIPT_DIR/lib.sh"

usage() {
  cat <<'EOF'
Usage: reproduce.sh --tag TAG [options]
       reproduce.sh --run ID [options]

  --tag TAG             Zed tag to transform (e.g. v1.25.2)
  --run ID              Reuse the tag recorded by collect.sh for this run
  --target NAME         Crate to check (raw name or *-gpui-unofficial);
                        repeatable. Defaults to the first failed crate in the
                        collected context, else gpui.
  --all                 Check every crate supported on this host (slow)
  --isolated            Also run `xtask test-isolated` for each target
  --examples            Also build gpui's examples (slow; implies a real build)
  --skip-transform      Reuse the existing crates/ tree
  --zed-path PATH       Use an existing zed checkout at TAG
  --release             Build xtask in release mode (CI parity; slower)
  --dry-run             Print the commands without running them
  -h, --help            Show this help

Environment:
  CI_FIX_CACHE          Cache root (default: <repo>/target/ci-fix)
  CARGO_TARGET_DIR      Shared cargo target dir (default: <cache>/cargo)
EOF
}

TAG=""
RUN=""
ZED_PATH_ARG=""
TARGETS=()
ALL=0
ISOLATED=0
EXAMPLES=0
SKIP_TRANSFORM=0
RELEASE=0
DRY_RUN=0

while [ $# -gt 0 ]; do
  case "$1" in
    --tag) TAG="${2:?--tag needs a value}"; shift 2 ;;
    --run) RUN="${2:?--run needs an id}"; shift 2 ;;
    --target) TARGETS+=("${2:?--target needs a value}"); shift 2 ;;
    --all|--full) ALL=1; shift ;;
    --isolated) ISOLATED=1; shift ;;
    --examples) EXAMPLES=1; shift ;;
    --skip-transform) SKIP_TRANSFORM=1; shift ;;
    --zed-path) ZED_PATH_ARG="${2:?--zed-path needs a value}"; shift 2 ;;
    --release) RELEASE=1; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown argument: $1 (try --help)" ;;
  esac
done

require_cmd cargo git jq

if [ -z "$TAG" ] && [ -n "$RUN" ]; then
  meta="$CACHE_DIR/$RUN/meta.json"
  if [ -f "$meta" ]; then
    TAG="$(jq -r '.zed_tag // empty' "$meta" 2>/dev/null || true)"
  fi
  if [ -z "$TAG" ] || [ "$TAG" = "unknown" ]; then
    die "no zed tag recorded for run $RUN; run scripts/ci-fix/collect.sh --run $RUN first, or pass --tag"
  fi
fi

[ -n "$TAG" ] || die "pass --tag TAG or --run ID (--help for details)"

if [ -n "$ZED_PATH_ARG" ]; then
  export CI_FIX_ZED_PATH="$ZED_PATH_ARG"
fi

cargo_env

# Resolve the zed checkout. In dry-run mode, do not download anything.
if [ "$DRY_RUN" -eq 1 ] && [ -z "${CI_FIX_ZED_PATH:-}" ] && [ ! -d "$CACHE_DIR/zed/$TAG/.git" ]; then
  ZED="$CACHE_DIR/zed/$TAG"
  info "dry-run: would clone zed $TAG into $ZED"
else
  ZED="$(ensure_zed "$TAG")"
fi

if [ "$RELEASE" -eq 1 ]; then
  XTASK=(cargo run --quiet --release -p xtask --)
else
  XTASK=(cargo run --quiet -p xtask --)
fi

run_cmd() {
  if [ "$DRY_RUN" -eq 1 ]; then
    printf '+ (cd %s && %s)\n' "$1" "${*:2}"
  else
    ( cd "$1" && "${@:2}" )
  fi
}

# ---------------------------------------------------------------------------
# Transform
# ---------------------------------------------------------------------------

if [ "$SKIP_TRANSFORM" -eq 0 ]; then
  info "Transforming zed $TAG (local path dependencies)..."
  if [ "$DRY_RUN" -eq 1 ]; then
    printf '+ %s transform --zed-tag %s --zed-path %s --local --output crates\n' "${XTASK[*]}" "$TAG" "$ZED"
  else
    "${XTASK[@]}" transform --zed-tag "$TAG" --zed-path "$ZED" --local --output crates
  fi
else
  info "Skipping transform (--skip-transform)"
fi

# ---------------------------------------------------------------------------
# Choose targets
# ---------------------------------------------------------------------------

if [ "$ALL" -eq 1 ]; then
  TARGETS=()
  if [ "$DRY_RUN" -eq 1 ]; then
    # The crate list is static; read the first CRATE_PUBLISH_ORDER array
    # without building xtask. Stop at its closing bracket so test fixtures and
    # later occurrences in the file are not picked up.
    crates="$(awk '
      /pub const CRATE_PUBLISH_ORDER/ { inside=1 }
      inside && /^];/ { exit }
      inside && /^[[:space:]]*"[^"]+",?[[:space:]]*$/ {
        line=$0
        sub(/^[[:space:]]*"/, "", line)
        sub(/",?[[:space:]]*$/, "", line)
        print line
      }
    ' "$REPO_ROOT/xtask/src/transform.rs")"
  else
    crates="$("${XTASK[@]}" list-crates)"
  fi
  while IFS= read -r entry; do
    [ -n "$entry" ] || continue
    raw="${entry##*/}"
    if host_supported "$raw"; then
      TARGETS+=("$raw")
    fi
  done <<< "$crates"
  info "Checking ${#TARGETS[@]} host-supported crate(s)"
fi

if [ "${#TARGETS[@]}" -eq 0 ]; then
  # Try to infer the failed crate from the collected error digest. The first
  # crate named in a rustc error is usually the one to reproduce.
  inferred=""
  if [ -n "$RUN" ]; then
    for candidate in "$CACHE_DIR/$RUN/errors.txt" "$CACHE_DIR/$RUN/failure.txt"; do
      [ -f "$candidate" ] || continue
      inferred="$(
        grep -ahoE '[a-zA-Z0-9_-]+-gpui-unofficial' "$candidate" 2>/dev/null \
          | head -1 || true
      )"
      [ -n "$inferred" ] && break
    done
  fi

  if [ -n "$inferred" ]; then
    TARGETS=("$inferred")
    info "Inferred target from context: $inferred"
  else
    TARGETS=("gpui")
    info "No target given; defaulting to gpui"
  fi
fi

# ---------------------------------------------------------------------------
# Check
# ---------------------------------------------------------------------------

start_epoch="$(date +%s)"
failed=0

for target in "${TARGETS[@]}"; do
  raw="$(raw_crate_name "$target")"
  unofficial="$(unofficial_name "$raw")"
  crate_dir="$REPO_ROOT/crates/$unofficial"

  if [ "$DRY_RUN" -eq 0 ] && [ ! -d "$crate_dir" ]; then
    warn "crate directory not found: $crate_dir"
    failed=1
    continue
  fi

  features="$(crate_features "$raw")"
  check_cmd=(cargo check)
  if [ -n "$features" ]; then
    check_cmd+=(--features "$features")
  fi

  info "cargo check $unofficial${features:+ (features: $features)}"
  if ! run_cmd "$crate_dir" "${check_cmd[@]}"; then
    err "check failed: $unofficial"
    failed=1
  fi

  if [ "$ISOLATED" -eq 1 ]; then
    info "isolated packaging test for $raw"
    if [ "$DRY_RUN" -eq 1 ]; then
      printf '+ %s test-isolated --crate %s\n' "${XTASK[*]}" "$raw"
    elif ! "${XTASK[@]}" test-isolated --crate "$raw"; then
      err "isolated packaging failed: $raw"
      failed=1
    fi
  fi

  if [ "$EXAMPLES" -eq 1 ] && [ "$raw" = "gpui" ]; then
    info "building gpui examples"
    run_cmd "$crate_dir" cargo build --examples || failed=1
  fi
done

elapsed=$(( $(date +%s) - start_epoch ))
info "finished in ${elapsed}s"

if [ -d "$CACHE_DIR" ]; then
  info "cache: $CACHE_DIR ($(human_size "$CACHE_DIR" 2>/dev/null || echo '?'))"
fi

if [ "$failed" -ne 0 ]; then
  err "reproduction found failures"
  exit 1
fi

ok "reproduction completed without errors"
