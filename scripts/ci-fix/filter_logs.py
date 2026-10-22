#!/usr/bin/env python3
"""Reduce GitHub Actions job logs to the lines that matter for a CI failure.

The raw logs stay on disk for humans. This script writes two much smaller
artifacts:

* an excerpt file containing the error lines plus a few lines of context, and
* a deduplicated error digest suitable for a model prompt.

Keeping the raw logs out of the model context is the single biggest cost
control in the local CI-fix workflow.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from pathlib import Path

ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z ?")

# Strong signals: always included in the digest.
STRONG_PATTERNS = [
    re.compile(r"error(?:\[E\d+\])?:"),
    re.compile(r"\bFAILED\b"),
    re.compile(r"could not compile"),
    re.compile(r"no matching package named"),
    re.compile(r"^Caused by:"),
    re.compile(r"panicked at"),
    re.compile(r"^##\[error\]"),
    re.compile(r"^Error:"),
    re.compile(r"failed to (?:build|compile|resolve|prepare|package|download|update)"),
    re.compile(r"thread '[^']+' panicked"),
    re.compile(r"assertion .* failed"),
    re.compile(r"^\s*--> "),
]

# Weaker signals: useful context for the excerpt, but not the digest on their own.
WEAK_PATTERNS = [
    re.compile(r"\b(?:warning|warn):"),
    re.compile(r"\bnot found\b", re.IGNORECASE),
    re.compile(r"\b(?:timeout|timed out)\b", re.IGNORECASE),
    re.compile(r"\b(?:rate limit|429)\b", re.IGNORECASE),
    re.compile(r"\b(?:permission denied|unauthorized|forbidden|403)\b", re.IGNORECASE),
    re.compile(r"\b(?:network|connection reset|tls|ssl)\b", re.IGNORECASE),
]

CONTEXT_BEFORE = 3
CONTEXT_AFTER = 3
MAX_DIGEST_LINES = 40


def clean(line: str) -> str:
    line = ANSI_RE.sub("", line)
    line = TIMESTAMP_RE.sub("", line)
    return line.rstrip()


def is_strong(line: str) -> bool:
    return any(p.search(line) for p in STRONG_PATTERNS)


def is_weak(line: str) -> bool:
    return any(p.search(line) for p in WEAK_PATTERNS)


def read_clean(path: Path) -> list[str]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        print(f"warning: cannot read {path}: {exc}", file=sys.stderr)
        return []
    return [clean(line) for line in text.splitlines()]


def select_ranges(lines: list[str], max_lines: int) -> list[tuple[int, int]]:
    hits = [i for i, line in enumerate(lines) if is_strong(line)]
    if not hits:
        hits = [i for i, line in enumerate(lines) if is_weak(line)]

    ranges: list[tuple[int, int]] = []
    for hit in hits:
        start = max(0, hit - CONTEXT_BEFORE)
        end = min(len(lines), hit + CONTEXT_AFTER + 1)
        if ranges and start <= ranges[-1][1]:
            ranges[-1] = (ranges[-1][0], max(ranges[-1][1], end))
        else:
            ranges.append((start, end))

    # If the filter found nothing useful, keep the tail of the log: the last
    # lines are usually where the failure surfaced.
    if not ranges:
        start = max(0, len(lines) - 80)
        ranges = [(start, len(lines))]

    # Enforce a line budget by dropping the middle ranges first, keeping the
    # beginning (often the first error) and the end (often the fatal one).
    budget = max_lines
    kept: list[tuple[int, int]] = []
    for rng in ranges:
        size = rng[1] - rng[0]
        if size >= budget:
            kept.append((rng[0], rng[0] + budget))
            budget = 0
            break
        kept.append(rng)
        budget -= size
    return kept


def digest(lines: list[str]) -> list[str]:
    counts: Counter[str] = Counter()
    for line in lines:
        if not is_strong(line):
            continue
        normalized = re.sub(r"\s+", " ", line).strip()
        # Source-location lines add noise without telling the agent what broke.
        if normalized.startswith((">", "-->", "|")):
            continue
        # GitHub's generic wrapper lines say nothing about the root cause.
        if re.search(r"Process completed with exit code|process didn't exit successfully", normalized):
            continue
        # Drop leading indentation and source-line markers, but keep the message.
        normalized = re.sub(r"^[|+\-\s]+", "", normalized)
        if not normalized or normalized in {"^", "|"}:
            continue
        counts[normalized] += 1

    ordered = sorted(counts.items(), key=lambda item: (-item[1], item[0].lower()))
    out: list[str] = []
    for line, count in ordered:
        suffix = f"  (x{count})" if count > 1 else ""
        out.append(f"{line}{suffix}")
        if len(out) >= MAX_DIGEST_LINES:
            break
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("logs", nargs="+", type=Path, help="raw job log files")
    parser.add_argument("--out", required=True, type=Path, help="filtered excerpt file")
    parser.add_argument("--signature", required=True, type=Path, help="error digest file")
    parser.add_argument("--max-lines", type=int, default=600, help="excerpt line budget")
    args = parser.parse_args()

    all_digest: list[str] = []
    excerpt_parts: list[str] = []
    total_kept = 0

    for path in args.logs:
        lines = read_clean(path)
        if not lines:
            continue

        ranges = select_ranges(lines, max(120, args.max_lines // max(1, len(args.logs))))
        excerpt_parts.append(f"### {path.name}")
        excerpt_parts.append("")
        for start, end in ranges:
            for line in lines[start:end]:
                excerpt_parts.append(line)
                total_kept += 1
            excerpt_parts.append("")
        all_digest.extend(digest(lines))

    if total_kept == 0:
        excerpt_parts.append("(no error-like lines found; see the raw logs)")

    # Deduplicate the per-job digests while preserving the first occurrence.
    seen: set[str] = set()
    unique_digest: list[str] = []
    for line in all_digest:
        if line not in seen:
            seen.add(line)
            unique_digest.append(line)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("\n".join(excerpt_parts).rstrip() + "\n", encoding="utf-8")
    args.signature.write_text("\n".join(unique_digest).rstrip() + "\n", encoding="utf-8")

    print(
        f"filtered {len(args.logs)} log(s): {total_kept} excerpt lines, "
        f"{len(unique_digest)} digest lines",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
