#!/usr/bin/env bash
# Collect a compact failure bundle for a GitHub Actions run.
#
# Examples:
#   scripts/ci-fix/collect.sh --latest
#   scripts/ci-fix/collect.sh --run 39684649841
#   scripts/ci-fix/collect.sh --issue 343
#
# Artifacts land in target/ci-fix/<run-id>/:
#   meta.json     run metadata (plus zed_tag)
#   logs/         raw failed-job logs (for humans; never sent to the model)
#   failure.txt   filtered error excerpt (sent to the model)
#   errors.txt    deduplicated error digest (sent to the model)
#   context.md    the prompt bundle consumed by the agent
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
source "$SCRIPT_DIR/lib.sh"

usage() {
  cat <<'EOF'
Usage: collect.sh [options]

  --latest              Use the most recent failed run (default)
  --run ID              Use a specific workflow run id
  --issue NUMBER        Read the run URL from a GitHub issue
  --repo OWNER/NAME     Repository (default: gpui-archipelago/gpui-unofficial)
  --workflow NAME       Workflow name for --latest (default: Sync Zed Releases)
  --out DIR             Output directory (default: target/ci-fix/<run-id>)
  --max-lines N         Failure excerpt line budget (default: 600)
  -h, --help            Show this help
EOF
}

RUN_ID=""
ISSUE=""
OUT=""
MAX_LINES=600

while [ $# -gt 0 ]; do
  case "$1" in
    --latest) shift ;;
    --run) RUN_ID="${2:?--run needs an id}"; shift 2 ;;
    --issue) ISSUE="${2:?--issue needs a number}"; shift 2 ;;
    --repo) REPO="${2:?--repo needs owner/name}"; shift 2 ;;
    --workflow) WORKFLOW_NAME="${2:?--workflow needs a name}"; shift 2 ;;
    --out) OUT="${2:?--out needs a path}"; shift 2 ;;
    --max-lines) MAX_LINES="${2:?--max-lines needs a number}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown argument: $1 (try --help)" ;;
  esac
done

require_cmd gh jq python3 git

if [ -n "$ISSUE" ]; then
  info "Reading issue #$ISSUE from $REPO..."
  issue_json="$(gh issue view "$ISSUE" --repo "$REPO" --json number,title,url,body,createdAt)"
  RUN_ID="$(
    printf '%s' "$issue_json" \
      | jq -r '.body' \
      | grep -oE 'actions/runs/[0-9]+' \
      | head -1 \
      | awk -F/ '{print $NF}' || true
  )"
  [ -n "$RUN_ID" ] || die "no workflow run URL found in issue #$ISSUE"
  info "Issue #$ISSUE points at run $RUN_ID"
fi

if [ -z "$RUN_ID" ]; then
  info "Looking for the latest failed '$WORKFLOW_NAME' run..."
  RUN_ID="$(
    gh run list --repo "$REPO" --workflow "$WORKFLOW_NAME" --status failure \
      --limit 1 --json databaseId --jq '.[0].databaseId // empty'
  )"
  [ -n "$RUN_ID" ] || die "no failed run found for '$WORKFLOW_NAME'; pass --run <id>"
fi

OUT="${OUT:-$CACHE_DIR/$RUN_ID}"
mkdir -p "$OUT/logs"

info "Fetching run $RUN_ID metadata..."
gh run view "$RUN_ID" --repo "$REPO" \
  --json databaseId,displayTitle,workflowName,event,status,conclusion,headBranch,headSha,createdAt,url,jobs \
  > "$OUT/meta.json"

RUN_URL="$(jq -r '.url // empty' "$OUT/meta.json")"
info "Run: $RUN_URL"

FAILED_JOBS="$(
  jq -r '.jobs[] | select(.conclusion == "failure" or .conclusion == "cancelled" or .conclusion == "timed_out") | "\(.databaseId)\t\(.name)"' \
    "$OUT/meta.json"
)"
[ -n "$FAILED_JOBS" ] || die "run $RUN_ID has no failed jobs (conclusion: $(jq -r '.conclusion' "$OUT/meta.json"))"

LOG_FILES=()
while IFS=$'\t' read -r job_id job_name; do
  [ -n "$job_id" ] || continue
  slug="$(sanitize_slug "$job_name")"
  [ -n "$slug" ] || slug="job-$job_id"
  log="$OUT/logs/$slug.log"

  info "Downloading logs for $job_name ($job_id)..."
  if ! gh api "repos/$REPO/actions/jobs/$job_id/logs" > "$log" 2>"$OUT/logs/.download-error"; then
    warn "API log download failed for $job_name; trying 'gh run view'"
    if ! gh run view "$RUN_ID" --repo "$REPO" --job "$job_id" --log > "$log" 2>>"$OUT/logs/.download-error"; then
      warn "Could not download logs for $job_name"
      rm -f "$log"
      continue
    fi
  fi

  if [ ! -s "$log" ]; then
    warn "Empty log for $job_name"
    rm -f "$log"
    continue
  fi
  LOG_FILES+=("$log")
done <<< "$FAILED_JOBS"
rm -f "$OUT/logs/.download-error"

[ "${#LOG_FILES[@]}" -gt 0 ] || die "no failed-job logs were downloaded"

# The check-release job prints the zed tag. It usually succeeds, so it is not
# in LOG_FILES; fetch its small log explicitly for reliable tag detection.
CHECK_RELEASE_ID="$(jq -r '.jobs[] | select(.name == "check-release") | .databaseId' "$OUT/meta.json" | head -1)"
if [ -n "$CHECK_RELEASE_ID" ]; then
  check_log="$OUT/logs/check-release.log"
  if [ ! -s "$check_log" ]; then
    gh run view "$RUN_ID" --repo "$REPO" --job "$CHECK_RELEASE_ID" --log \
      > "$check_log" 2>/dev/null || rm -f "$check_log"
  fi
fi

# Fall back to the run title, then to a version mentioned anywhere in the logs.
ZED_TAG="$(
  grep -ahoE '(Latest zed tag|Using manual tag): v[0-9]+\.[0-9]+\.[0-9]+' "$OUT/logs/check-release.log" 2>/dev/null \
    | head -1 | grep -oE 'v[0-9]+\.[0-9]+\.[0-9]+' || true
)"
if [ -z "$ZED_TAG" ]; then
  ZED_TAG="$(
    grep -ahoE '(Latest zed tag|Using manual tag): v[0-9]+\.[0-9]+\.[0-9]+' "$OUT"/logs/*.log 2>/dev/null \
      | head -1 | grep -oE 'v[0-9]+\.[0-9]+\.[0-9]+' || true
  )"
fi
if [ -z "$ZED_TAG" ]; then
  ZED_TAG="$(jq -r '.displayTitle // empty' "$OUT/meta.json" | grep -oE 'v[0-9]+\.[0-9]+\.[0-9]+' | head -1 || true)"
fi
if [ -z "$ZED_TAG" ]; then
  ZED_TAG="$(grep -ahoE 'v[0-9]+\.[0-9]+\.[0-9]+' "$OUT"/logs/*.log 2>/dev/null | sort | uniq -c | sort -rn | head -1 | grep -oE 'v[0-9]+\.[0-9]+\.[0-9]+' || true)"
fi
if [ -n "$ZED_TAG" ]; then
  info "Detected zed tag: $ZED_TAG"
else
  warn "Could not detect the zed tag; pass --tag to reproduce.sh"
  ZED_TAG="unknown"
fi

python3 "$SCRIPT_DIR/filter_logs.py" \
  --out "$OUT/failure.txt" \
  --signature "$OUT/errors.txt" \
  --max-lines "$MAX_LINES" \
  "${LOG_FILES[@]}"

python3 "$SCRIPT_DIR/build_context.py" \
  --meta "$OUT/meta.json" \
  --failures "$OUT/failure.txt" \
  --errors "$OUT/errors.txt" \
  --run "$RUN_ID" \
  --run-url "$RUN_URL" \
  --repo "$REPO" \
  --zed-tag "$ZED_TAG" \
  --repo-root "$REPO_ROOT" \
  --out "$OUT/context.md"

# Persist the tag next to the metadata so reproduce.sh --run can find it.
jq --arg tag "$ZED_TAG" '. + {zed_tag: $tag}' "$OUT/meta.json" > "$OUT/meta.json.tmp"
mv "$OUT/meta.json.tmp" "$OUT/meta.json"

ln -sfn "$RUN_ID" "$CACHE_DIR/latest" 2>/dev/null || true

info "Context bundle: $OUT/context.md"
info "Raw logs:       $OUT/logs/"
info "Filtered:       $OUT/failure.txt"
info "Error digest:   $OUT/errors.txt"
printf '\n%s--- error digest ---%s\n' "$C_INFO" "$C_RESET"
sed -n '1,40p' "$OUT/errors.txt" || true
