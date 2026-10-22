#!/usr/bin/env bash
# Shared helpers for the local CI-fix workflow.
#
# Source this file from the other scripts in this directory. It resolves the
# repository root, sets up a small cache under `target/ci-fix`, and provides the
# crate-name / target mappings used by both the xtask and the GitHub workflows.

set -euo pipefail

CI_FIX_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="${CI_FIX_REPO_ROOT:-$(git -C "$CI_FIX_SCRIPT_DIR" rev-parse --show-toplevel 2>/dev/null || true)}"
if [ -z "${REPO_ROOT:-}" ]; then
  REPO_ROOT="$(cd "$CI_FIX_SCRIPT_DIR/../.." && pwd)"
fi

REPO="${CI_FIX_REPO:-gpui-archipelago/gpui-unofficial}"
WORKFLOW_NAME="${CI_FIX_WORKFLOW:-Sync Zed Releases}"
CACHE_DIR="${CI_FIX_CACHE:-$REPO_ROOT/target/ci-fix}"

if [ -t 1 ]; then
  C_RESET=$'\033[0m'
  C_INFO=$'\033[36m'
  C_WARN=$'\033[33m'
  C_ERR=$'\033[31m'
  C_OK=$'\033[32m'
else
  C_RESET=""
  C_INFO=""
  C_WARN=""
  C_ERR=""
  C_OK=""
fi

info() { printf '%s[ci-fix]%s %s\n' "$C_INFO" "$C_RESET" "$*" >&2; }
warn() { printf '%s[ci-fix]%s %s\n' "$C_WARN" "$C_RESET" "$*" >&2; }
err()  { printf '%s[ci-fix]%s %s\n' "$C_ERR" "$C_RESET" "$*" >&2; }
ok()   { printf '%s[ci-fix]%s %s\n' "$C_OK" "$C_RESET" "$*" >&2; }
die()  { err "$*"; exit 1; }

require_cmd() {
  local c
  for c in "$@"; do
    command -v "$c" >/dev/null 2>&1 || die "required command not found: $c"
  done
}

# Turn arbitrary text (an error line, a job name) into a branch-name-safe slug.
sanitize_slug() {
  printf '%s' "$1" \
    | tr '[:upper:]' '[:lower:]' \
    | sed -E 's/`[^`]*`//g; s/[^a-z0-9]+/-/g; s/^-+//; s/-+$//' \
    | cut -c1-48
}

# unofficial_name <raw-or-unofficial>
#   gpui            -> gpui-unofficial
#   gpui_wgpu       -> gpui-wgpu-gpui-unofficial
#   collections     -> collections-gpui-unofficial
unofficial_name() {
  local name="${1##*/}"
  name="${name%.rs}"
  case "$name" in
    gpui-unofficial) printf '%s' "$name"; return ;;
    *-gpui-unofficial) printf '%s' "$name"; return ;;
    gpui) printf 'gpui-unofficial'; return ;;
  esac
  printf '%s-gpui-unofficial' "${name//_/-}"
}

# raw_crate_name <raw-or-unofficial>
#   gpui-unofficial                 -> gpui
#   gpui-wgpu-gpui-unofficial       -> gpui_wgpu
raw_crate_name() {
  local name="${1##*/}"
  case "$name" in
    gpui-unofficial) printf 'gpui'; return ;;
    *-gpui-unofficial) name="${name%-gpui-unofficial}" ;;
  esac
  printf '%s' "${name//-/_}"
}

# host_supported <raw-crate-name>
host_supported() {
  local raw="$1"
  case "$raw" in
    gpui_apple|gpui_macos) [ "$(uname -s)" = "Darwin" ] ;;
    gpui_linux) [ "$(uname -s)" = "Linux" ] ;;
    gpui_windows) case "$(uname -s)" in MINGW*|MSYS*|CYGWIN*) return 0 ;; *) return 1 ;; esac ;;
    gpui_web) return 1 ;;
    *) return 0 ;;
  esac
}

# crate_features <raw-crate-name> -> comma-separated feature list (may be empty)
crate_features() {
  local raw="$1"
  if [ "$(uname -s)" = "Linux" ] && { [ "$raw" = "gpui" ] || [ "$raw" = "gpui_linux" ]; }; then
    printf 'wayland,x11'
  fi
}

# Keep cargo's scratch space out of the generated crates and reuse it between
# runs. The transformed tree is deleted on every transform, but the dependency
# artifacts in this directory are still reusable.
cargo_env() {
  export CARGO_TARGET_DIR="${CARGO_TARGET_DIR:-$CACHE_DIR/cargo}"
  export CARGO_INCREMENTAL="${CARGO_INCREMENTAL:-1}"
  export CARGO_NET_RETRY="${CARGO_NET_RETRY:-5}"
  export CARGO_TERM_COLOR="${CARGO_TERM_COLOR:-never}"
  mkdir -p "$CARGO_TARGET_DIR"
}

# ensure_zed <tag> -> prints the path to a zed checkout at that tag.
#
# Reuses a shallow clone under target/ci-fix/zed/<tag> so repeated runs do not
# re-download zed. Set CI_FIX_ZED_PATH to use an existing checkout instead.
ensure_zed() {
  local tag="$1"
  if [ -n "${CI_FIX_ZED_PATH:-}" ]; then
    [ -d "$CI_FIX_ZED_PATH/.git" ] || die "CI_FIX_ZED_PATH is not a git checkout: $CI_FIX_ZED_PATH"
    printf '%s' "$CI_FIX_ZED_PATH"
    return
  fi

  local dest="$CACHE_DIR/zed/$tag"
  if [ -d "$dest/.git" ]; then
    if git -C "$dest" rev-parse --verify HEAD >/dev/null 2>&1; then
      printf '%s' "$dest"
      return
    fi
    warn "incomplete zed checkout at $dest; re-cloning" >&2
    rm -rf "$dest"
  fi

  mkdir -p "$(dirname "$dest")"
  info "Cloning zed $tag into $dest (depth 1)..." >&2
  git clone --quiet --depth=1 --branch "$tag" \
    https://github.com/zed-industries/zed.git "$dest" >&2
  printf '%s' "$dest"
}

# xtask_run <args...>
xtask_run() {
  cargo run --quiet -p xtask -- "$@"
}

# Print a compact size for a file or directory.
human_size() {
  du -sh "$1" 2>/dev/null | cut -f1
}
