#!/usr/bin/env python3
"""Build the compact prompt bundle for the local CI-fix agent.

Inputs are the artifacts written by ``collect.sh``: run metadata, the filtered
failure excerpt and the error digest. The output is a single Markdown file that
fits comfortably in a model context window (typically 3-8k tokens). Raw logs
are deliberately not included.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Files an agent is most likely to need for a transform / publish / workflow
# failure. We list sizes rather than contents so the agent can decide what to
# read.
RELEVANT_FILES = [
    "xtask/src/transform.rs",
    "xtask/src/publish.rs",
    "xtask/src/test_isolated.rs",
    "xtask/src/verify.rs",
    "xtask/src/main.rs",
    "xtask/src/bump.rs",
    ".github/workflows/sync.yml",
    "README.md",
]

FAILURE_PLAYBOOK = """\
Common failure classes in this repository:

- **Missing crate metadata** (description/license/repository) -> fix the
  package section written by `xtask/src/transform.rs`.
- **Unresolved workspace dependency** -> the crate is missing from
  `CRATE_PUBLISH_ORDER` or its path handling in `transform.rs` is wrong.
- **Dependency not found on crates.io** -> publish ordering / propagation
  issue in `xtask/src/publish.rs`.
- **Build error in a generated crate** -> the transform produced a manifest
  or source tree that no longer matches the upstream crate. Fix the transform
  (or its patches), never the generated file under `crates/`.
- **Isolated packaging failure** -> `cargo package` could not resolve a
  sibling; fix the manifest transform or `test-isolated` vendoring logic.
- **Permission / workflow error** -> fix the YAML in `.github/workflows/`.
- **crates.io rate limiting or propagation delay** -> adjust the delays in
  `publish.rs`; this is usually not a code bug.
"""


def read_text(path: Path, limit: int = 2_000_000) -> str:
    try:
        data = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return data[:limit]


def failed_steps(job: dict) -> list[str]:
    out = []
    for step in job.get("steps", []) or []:
        if step.get("conclusion") in {"failure", "cancelled", "timed_out"}:
            out.append(step.get("name", "<unnamed>"))
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--meta", required=True, type=Path)
    parser.add_argument("--failures", required=True, type=Path)
    parser.add_argument("--errors", required=True, type=Path)
    parser.add_argument("--run", required=True)
    parser.add_argument("--run-url", required=True)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--zed-tag", default="unknown")
    parser.add_argument("--repo-root", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()

    meta = json.loads(args.meta.read_text(encoding="utf-8"))
    errors = read_text(args.errors).strip()
    failures = read_text(args.failures).strip()

    jobs = meta.get("jobs", []) or []
    failed_jobs = [j for j in jobs if j.get("conclusion") not in (None, "success", "skipped")]
    skipped_jobs = [j for j in jobs if j.get("conclusion") == "skipped"]

    lines: list[str] = []
    lines.append("# Local CI fix context")
    lines.append("")
    lines.append(
        "A GitHub Actions run failed. Diagnose the root cause in this repository "
        "and make the smallest fix that addresses it."
    )
    lines.append("")
    lines.append("## Run")
    lines.append("")
    lines.append(f"- Run: `{args.run}`")
    lines.append(f"- URL: {args.run_url}")
    lines.append(f"- Repo: `{args.repo}`")
    lines.append(f"- Workflow: {meta.get('workflowName', 'unknown')}")
    lines.append(f"- Event: {meta.get('event', 'unknown')}")
    lines.append(f"- Conclusion: {meta.get('conclusion', 'unknown')}")
    lines.append(f"- Branch: `{meta.get('headBranch', 'unknown')}`")
    lines.append(f"- Head SHA: `{meta.get('headSha', 'unknown')}`")
    lines.append(f"- Created: {meta.get('createdAt', 'unknown')}")
    lines.append(f"- Zed tag: `{args.zed_tag}`")
    lines.append("")

    lines.append("## Jobs")
    lines.append("")
    lines.append("| job | conclusion | failed steps |")
    lines.append("| --- | --- | --- |")
    for job in jobs:
        steps = ", ".join(failed_steps(job)) or "-"
        if len(steps) > 160:
            steps = steps[:157] + "..."
        lines.append(f"| {job.get('name', '?')} | {job.get('conclusion', '?')} | {steps} |")
    lines.append("")

    if failed_jobs:
        lines.append(
            "Failed jobs: "
            + ", ".join(f"`{j.get('name', '?')}`" for j in failed_jobs)
        )
        lines.append("")

    lines.append("## Error digest")
    lines.append("")
    if errors:
        lines.append("```text")
        lines.append(errors)
        lines.append("```")
    else:
        lines.append("(no error-like lines were extracted)")
    lines.append("")

    lines.append("## Failure excerpt")
    lines.append("")
    lines.append(
        "Filtered from the failed jobs' logs (timestamps and unrelated output removed). "
        "Raw logs are beside this file under `logs/` if you need more."
    )
    lines.append("")
    lines.append("```text")
    lines.append(failures or "(empty)")
    lines.append("```")
    lines.append("")

    lines.append("## Diagnosis playbook")
    lines.append("")
    lines.append(FAILURE_PLAYBOOK.rstrip())
    lines.append("")

    lines.append("## Relevant files")
    lines.append("")
    for rel in RELEVANT_FILES:
        path = args.repo_root / rel
        if path.exists():
            size = path.stat().st_size
            lines.append(f"- `{rel}` ({size:,} bytes)")
        else:
            lines.append(f"- `{rel}` (missing)")
    lines.append("")

    lines.append("## Suggested commands")
    lines.append("")
    lines.append("Prefer the helper scripts; they reuse caches and limit the build scope.")
    lines.append("")
    lines.append("```sh")
    lines.append(f"# reproduce the failure for the tagged release")
    lines.append(f"scripts/ci-fix/reproduce.sh --run {args.run} --target <crate>")
    lines.append("")
    lines.append("# run the cheap verification suite")
    lines.append(f"scripts/ci-fix/verify.sh --run {args.run}")
    lines.append("")
    lines.append("# inspect the generated tree (never edit it)")
    lines.append("ls crates/")
    lines.append("```")
    lines.append("")

    lines.append("## Rules")
    lines.append("")
    lines.append("- Never modify anything under `crates/`; it is generated by `xtask transform`.")
    lines.append("- Keep the change minimal and specific to this failure.")
    lines.append("- Prefer fixing `xtask/src/transform.rs`, `xtask/src/publish.rs`, or workflow YAML.")
    lines.append("- Do not commit, push, or open a PR; `submit.sh` does that after review.")
    lines.append("- If this is an external outage (crates.io, GitHub, network), report it instead of coding.")
    lines.append("")

    if skipped_jobs:
        lines.append(
            "Note: these jobs were skipped because an earlier job failed: "
            + ", ".join(f"`{j.get('name', '?')}`" for j in skipped_jobs)
        )
        lines.append("")

    text = "\n".join(lines).rstrip() + "\n"
    args.out.write_text(text, encoding="utf-8")

    approx_tokens = len(text) // 4
    print(
        f"wrote {args.out} ({len(text):,} bytes, ~{approx_tokens:,} tokens)",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
