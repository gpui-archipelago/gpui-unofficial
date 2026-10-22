#!/usr/bin/env bash
# Commit the current working tree and open a CI-fix pull request.
#
# Safe by default: without --push it prints exactly what it would do and exits.
# Run it only after reviewing the diff and a green `verify.sh`.
#
# Examples:
#   scripts/ci-fix/submit.sh --run 39684649841
#   scripts/ci-fix/submit.sh --run 39684649841 --push
#   scripts/ci-fix/submit.sh --run 39684649841 --push --no-draft
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
source "$SCRIPT_DIR/lib.sh"

usage() {
  cat <<'EOF'
Usage: submit.sh --run ID [options]

  --run ID              Failure run id (used in the branch name and PR body)
  --title TEXT          Short description (default: derived from the error digest)
  --body-file PATH      PR body file (default: generated from the context bundle)
  --branch NAME         Branch name (default: ci-fix/<slug>-<run>)
  --push                Actually commit, push, and open the PR
  --no-draft            Open a ready-for-review PR instead of a draft
  --force               Ignore an existing open ci-fix PR
  -h, --help            Show this help

Without --push this script is a dry run.
EOF
}

RUN=""
TITLE=""
BODY_FILE=""
BRANCH=""
PUSH=0
DRAFT=1
FORCE=0

while [ $# -gt 0 ]; do
  case "$1" in
    --run) RUN="${2:?--run needs an id}"; shift 2 ;;
    --title) TITLE="${2:?--title needs text}"; shift 2 ;;
    --body-file) BODY_FILE="${2:?--body-file needs a path}"; shift 2 ;;
    --branch) BRANCH="${2:?--branch needs a name}"; shift 2 ;;
    --push) PUSH=1; shift ;;
    --no-draft) DRAFT=0; shift ;;
    --force) FORCE=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown argument: $1 (try --help)" ;;
  esac
done

[ -n "$RUN" ] || die "pass --run ID (--help for details)"
require_cmd git
if [ "$PUSH" -eq 1 ]; then
  require_cmd gh
  gh auth status >/dev/null 2>&1 || die "gh is not authenticated; run 'gh auth login'"
fi

RUN_DIR="$CACHE_DIR/$RUN"
CONTEXT="$RUN_DIR/context.md"
ERRORS="$RUN_DIR/errors.txt"

if [ -z "$TITLE" ]; then
  if [ -f "$ERRORS" ]; then
    # Prefer the crate that failed to compile: it makes a useful branch name
    # and avoids "Process completed with exit code ..." as the title.
    crate="$(grep -aoE 'could not compile `[^`]+`' "$ERRORS" 2>/dev/null | head -1 | sed -E 's/.*`([^`]+)`.*/\1/' || true)"
    if [ -n "$crate" ]; then
      TITLE="fix $crate build failure"
    else
      first="$(grep -vE '^[[:space:]]*$|^##\[error\]Process completed|^Process completed with exit|^Caused by:|^FAILED$|^error: failed to prepare' "$ERRORS" 2>/dev/null | head -1 || true)"
      TITLE="$(sanitize_slug "$first" | tr '-' ' ' | cut -c1-72)"
    fi
  fi
  if [ -z "$TITLE" ]; then
    TITLE="fix CI failure for run $RUN"
  fi
fi

if [ -z "$BRANCH" ]; then
  slug="$(sanitize_slug "$TITLE")"
  [ -n "$slug" ] || slug="ci-failure"
  BRANCH="ci-fix/$slug-$RUN"
fi

# ---------------------------------------------------------------------------
# Existing PR guard
# ---------------------------------------------------------------------------

if [ "$FORCE" -eq 0 ]; then
  existing="$(
    {
      gh pr list --repo "$REPO" --state open --label ci-fix --json number,title,url --jq '.[] | "#\(.number) \(.title) \(.url)"' 2>/dev/null || true
      gh pr list --repo "$REPO" --state open --search '[CI Fix] in:title' --json number,title,url --jq '.[] | "#\(.number) \(.title) \(.url)"' 2>/dev/null || true
    } | sort -u
  )"
  if [ -n "$existing" ]; then
    warn "an open CI-fix PR already exists:"
    printf '%s\n' "$existing" >&2
    die "not creating a duplicate (use --force to override)"
  fi
fi

# ---------------------------------------------------------------------------
# Working tree
# ---------------------------------------------------------------------------

status="$(git -C "$REPO_ROOT" status --porcelain)"
if [ -z "$status" ]; then
  die "working tree is clean; nothing to submit"
fi

current_branch="$(git -C "$REPO_ROOT" rev-parse --abbrev-ref HEAD)"
if [ "$current_branch" != "$BRANCH" ]; then
  if git -C "$REPO_ROOT" rev-parse --verify --quiet "$BRANCH" >/dev/null; then
    die "branch '$BRANCH' already exists locally; check it out or pass --branch"
  fi
fi

# ---------------------------------------------------------------------------
# PR body
# ---------------------------------------------------------------------------

if [ -z "$BODY_FILE" ]; then
  BODY_FILE="$(mktemp "${TMPDIR:-/tmp}/ci-fix-pr-body.XXXXXX")"
  {
    echo "Local CI fix for run [\`$RUN\`](https://github.com/$REPO/actions/runs/$RUN)."
    echo
    if [ -f "$CONTEXT" ]; then
      echo "## Failure context"
      echo
      sed -n '/^## Error digest/,/^## Failure excerpt/p' "$CONTEXT" | sed '$d'
      echo
    fi
    echo "## Changes"
    echo
    echo '```'
    git -C "$REPO_ROOT" status --short
    echo '```'
    echo
    echo '```'
    git -C "$REPO_ROOT" diff --stat
    echo '```'
    echo
    echo "## Verification"
    echo
    echo "Run \`scripts/ci-fix/verify.sh --run $RUN\` before review. Paste the summary here:"
    echo
    echo '```'
    echo "(not captured automatically)"
    echo '```'
  } > "$BODY_FILE"
fi

# ---------------------------------------------------------------------------
# Dry run / execute
# ---------------------------------------------------------------------------

printf '%s--- submit plan ---%s\n' "$C_INFO" "$C_RESET"
printf '  repo:    %s\n' "$REPO"
printf '  branch:  %s\n' "$BRANCH"
printf '  title:   [CI Fix] %s\n' "$TITLE"
printf '  body:    %s\n' "$BODY_FILE"
printf '  mode:    %s\n' "$([ "$PUSH" -eq 1 ] && echo 'commit + push + PR' || echo 'dry run')"
echo
git -C "$REPO_ROOT" status --short
echo

if [ "$PUSH" -eq 0 ]; then
  info "dry run; re-run with --push to commit and open the PR"
  exit 0
fi

if [ "$current_branch" != "$BRANCH" ]; then
  info "creating branch $BRANCH"
  git -C "$REPO_ROOT" checkout -b "$BRANCH"
fi

info "committing"
git -C "$REPO_ROOT" add -A
git -C "$REPO_ROOT" commit -m "Fix: $TITLE" -m "Local CI fix for run $RUN."

info "pushing"
git -C "$REPO_ROOT" push -u origin "$BRANCH"

# Use owner:branch when origin is a fork, plain branch when it is the target repo.
origin_url="$(git -C "$REPO_ROOT" remote get-url origin 2>/dev/null || true)"
if printf '%s' "$origin_url" | grep -q "[/:]$REPO\(\.git\)\?$"; then
  head_ref="$BRANCH"
else
  owner="$(gh api user --jq .login)"
  head_ref="$owner:$BRANCH"
fi

pr_args=(--repo "$REPO" --base main --head "$head_ref" --title "[CI Fix] $TITLE" --body-file "$BODY_FILE")
if [ "$DRAFT" -eq 1 ]; then
  pr_args+=(--draft)
fi

info "opening pull request"
pr_url="$(gh pr create "${pr_args[@]}")"
printf '%s\n' "$pr_url"

# Labels are best-effort: they may not exist in a fresh repository.
if ! gh pr edit "$pr_url" --add-label automation --add-label ci-fix >/dev/null 2>&1; then
  warn "could not add labels automation/ci-fix (they may not exist in $REPO)"
fi

ok "opened $pr_url"
