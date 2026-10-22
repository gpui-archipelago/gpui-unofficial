---
name: ci-fix-local
description: Run the former gh-aw CI Fix agent locally when a Sync/Release workflow fails. Collect a failed GitHub Actions run into a compact context bundle, reproduce it with a shallow zed checkout and a single-crate cargo check, diagnose and patch the transform/publish workflow, verify cheaply, and optionally open a draft PR from the local checkout. Use when asked to fix a failed gpui-unofficial release, investigate a release-failure issue such as #343, or run the CI fix workflow without GitHub Actions or ANTHROPIC_API_KEY.
---

# Local CI fix

The repository used to run a `gh-aw` "CI Fix" agent on `workflow_run.failure`
(removed in [#341](https://github.com/gpui-archipelago/gpui-unofficial/pull/341)).
This skill replaces it with local scripts plus either the current coding agent
or an optional headless DeepSeek loop. Nothing runs in GitHub Actions; there is
no `ANTHROPIC_API_KEY` and no GitHub MCP server.

`sync.yml` still opens an issue when a release fails (for example
[#343](https://github.com/gpui-archipelago/gpui-unofficial/issues/343)). The
local workflow starts from that issue or from the failed run id.

## Quick start

Run these from the repository root. The scripts are
`scripts/ci-fix/*`; no install step is needed.

```sh
# 1. Collect the failed run (issue or run id; --latest also works)
scripts/ci-fix/collect.sh --issue 343
#    -> target/ci-fix/<run-id>/context.md  (~5-8k tokens)

# 2. Reproduce cheaply: shallow zed clone + transform + one cargo check
scripts/ci-fix/reproduce.sh --run <run-id>

# 3. Fix the transform / publish code, then verify
scripts/ci-fix/verify.sh --run <run-id>

# 4. Review the diff and open a draft PR (dry run without --push)
scripts/ci-fix/submit.sh --run <run-id> --push
```

The collector infers the zed tag and the first failing crate. Pass
`--target <crate>` to reproduce a different one. `--all` runs the host matrix
and `--isolated` runs the packaging sandbox; both are much slower.

## Prerequisites

| tool | why |
| --- | --- |
| `cargo` / `rustc` | build `xtask` and the transformed crates |
| `gh`, authenticated | read the run, its jobs, and its logs (`gh auth status`) |
| `jq` | parse run metadata and the recorded tag |
| `python3` 3.9+ | log filtering, context building, cost accounting |
| `DEEPSEEK_API_KEY` | only for the headless loop; not needed for the interactive path |

On Linux the main `gpui` check also needs the wayland/X11 development
packages listed in `.github/workflows/sync.yml`; a `gpui-wgpu` check usually
does not.

## Two ways to run the agent

### 1. Interactive (recommended)

Point the coding agent at this skill and say:

> Use the ci-fix-local skill to fix run `<run-id>` (or issue #343).

The agent runs `collect.sh`, reads `target/ci-fix/<run-id>/context.md`, edits
the source, and runs `verify.sh`. It must not commit or push.

### 2. Headless DeepSeek

Set `DEEPSEEK_API_KEY` and run the bundled loop:

```sh
python3 scripts/ci-fix/deepseek_agent.py --run <run-id>
# or, after collect.sh:
python3 scripts/ci-fix/deepseek_agent.py --context target/ci-fix/<run-id>/context.md
```

Useful flags:

| flag | default | meaning |
| --- | --- | --- |
| `--model` | `deepseek-flash` | `deepseek-flash` or `deepseek-v4-pro` |
| `--max-turns` | `10` | hard cap on model turns |
| `--max-output-tokens` | `2048` | per response |
| `--budget-usd` | `0.10` | stop when estimated spend exceeds this |
| `--thinking` | off | enable thinking mode (costs more) |
| `--dry-run` | off | show prompt size and tools without calling the API |

The loop has no shell: it can read/search/write repository files and run an
allow-list of commands (`cargo check/build/test`, read-only `git`, `gh run
view`, the `scripts/ci-fix/*` helpers). It refuses to write `crates/`, `.git/`,
or `target/`, and it cannot commit, push, or publish. Review its diff, then run
`verify.sh` and `submit.sh` yourself.

## Cost estimate

Prices below are DeepSeek's published USD per 1M tokens, captured 2026-10-22
from <https://api-docs.deepseek.com/quick_start/pricing/>. Peak is 01:00-04:00
and 06:00-10:00 UTC on weekdays; off-peak is half price.

| scenario | turns | cumulative input | output | `deepseek-flash` off-peak | peak |
| --- | ---: | ---: | ---: | ---: | ---: |
| lean | 8 | 60k | 8k | ~$0.007 | ~$0.013 |
| typical | 12 | 180k | 12k | ~$0.013 | ~$0.026 |
| heavy | 20 | 600k | 25k | ~$0.030 | ~$0.060 |

`deepseek-v4-pro` is roughly 4x the input and 3x the output price. Reproduce
the table with:

```sh
scripts/ci-fix/estimate-cost.sh --scenarios
scripts/ci-fix/estimate-cost.sh --usage target/ci-fix/<run-id>/usage.json
```

The actual cost is printed by `deepseek_agent.py` and written to
`target/ci-fix/<run-id>/usage.json`. The interactive path costs nothing beyond
the model you are already using.

### What keeps the cost down

- Raw job logs stay on disk. `collect.sh` sends only a filtered excerpt and a
  deduplicated error digest (the sample run produced ~27 KB / ~6.8k tokens
  from ~130 KB of logs).
- The agent gets targeted `read_file`/`search` tools, and tool output is
  truncated to 24 KB.
- `reproduce.sh` reuses a shallow zed clone and a shared cargo target dir, and
  defaults to one `cargo check` instead of the full four-platform matrix.
- `verify.sh` defaults to `cargo test -p xtask` plus one target check; the
  expensive isolated packaging and example builds are opt-in.
- The DeepSeek loop defaults to 10 turns, 2k output tokens/turn, no thinking,
  and a $0.10 budget cap.

## What the collector produces

```
target/ci-fix/<run-id>/
├── meta.json     # run metadata, jobs, detected zed_tag
├── context.md    # compact prompt bundle (send this to the model)
├── errors.txt    # deduplicated error digest
├── failure.txt   # filtered excerpt with context lines
├── logs/         # raw job logs (humans only)
└── usage.json    # written by deepseek_agent.py
```

`target/` is already gitignored. `target/ci-fix/latest` points at the most
recently collected run.

## Diagnosis playbook

1. Fix the **first** error, not the cascade. In a release run one crate failing
   to compile makes every dependent crate fail isolated packaging.
2. Match the symptom to the owner:
   - missing `description`/`license`/`repository` -> package metadata in
     `xtask/src/transform.rs`;
   - `no matching package named ...-gpui-unofficial` -> `CRATE_PUBLISH_ORDER`,
     path mapping, or a crate the transform skipped;
   - a git-only dependency resolved to the wrong crates.io version -> check
     `known_git_dep_version()` in `xtask/src/transform.rs`. Zed pins several
     crates to git revisions with no `version` field; the transform substitutes
     a hardcoded crates.io version. If upstream's pinned revision is newer than
     that version, the generated crate compiles against the wrong API;
   - changed upstream API (`E0433`, `E0560`, `E0599`) in a generated crate ->
     usually a vendored file, a dropped dependency feature, or a transform
     patch that no longer matches; fix the transform, not `crates/`;
   - `cargo package`/isolated failure -> `xtask/src/test_isolated.rs` vendoring
     or a manifest the transform emitted;
   - permission/HTTP 403 -> workflow `permissions`;
   - crates.io 429 or propagation delay -> timing in `publish.rs`.
3. Search before reading: `rg 'fn transform_crate' xtask/src`, then read the
   function range. Before adding a new workaround, check
   [`docs/upstream-patches.md`](../../../docs/upstream-patches.md): extend an
   existing patch if it is the same shape, and record any new one.
4. Reproduce with `--target`, edit minimally, re-run, then `verify.sh`.
5. If it is an external outage, stop and report it; do not code around it.

The current failure (run `39684649841`, issue #343) is a good example. The
digest shows `gpui-wgpu-gpui-unofficial` failing with `E0433`/`E0560`/`E0599`
against `wgpu`, followed by dependent crates failing packaging because the wgpu
crate never produced a package. The first crate is the one to investigate.

The concrete cause is visible in zed `v1.25.2`:

- zed's root `Cargo.toml` pins `wgpu = { git = "https://github.com/gfx-rs/wgpu",
  rev = "3b3506ea...", default-features = false }` with **no version**;
- `known_git_dep_version()` in `xtask/src/transform.rs` substitutes
  `wgpu@29.0.1`, while crates.io's newest is `30.0.1`;
- the pinned revision contains APIs added after `30.0.1`, so the substituted
  crate cannot compile. `30.0.1` already has `SurfaceColorSpace`,
  `SurfaceConfiguration::color_space`, `apply_limit_buckets`, and
  `Queue::present`; the pinned revision additionally has `QueueDescriptor`,
  `DeviceDescriptor::default_queue`, and `TextureFormat::has_srgb_suffix`,
  which `30.0.1` does not.

Bumping the mapping to `30.0.1` fixes the bulk of the errors; the remaining
three need a small vendored-source patch in `transform.rs` (adapt the new
descriptor fields/method), or a newer wgpu release. Do not "fix" the generated
crate; the change belongs in the transform (or in a documented decision to wait
for a wgpu release).

## Constraints

- Never edit `crates/`; it is regenerated by `xtask transform`.
- Never `cargo publish`, `git push`, or open a PR from the agent loop.
- Do not read `logs/` when the filtered excerpt is enough.
- `submit.sh` refuses to create a duplicate when an open `ci-fix` / `[CI Fix]`
  PR already exists; use `--force` to override.
- `submit.sh` is a dry run unless you pass `--push`; the PR is a draft unless
  you pass `--no-draft`.
- Every new transform/publish/packaging workaround must be recorded in
  [`docs/upstream-patches.md`](../../../docs/upstream-patches.md) and covered by
  a fixture test. The ledger explains what we changed on top of Zed and when
  each patch can be removed.

## Troubleshooting

- **`collect.sh --latest` finds nothing**: pass `--run <id>` or `--issue <n>`.
  `--workflow` selects a different workflow.
- **`reproduce.sh` cannot find the tag**: `collect.sh` records it in
  `meta.json`; pass `--tag v1.25.2` explicitly if the log did not contain it.
- **Disk pressure**: the cache is `target/ci-fix/` (shallow zed clone + cargo
  artifacts). Delete a single tag with
  `rm -rf target/ci-fix/zed/<tag>`; nothing else depends on it.
- **System dependencies**: the Linux check needs wayland/xkbcommon/X11 dev
  packages for the `gpui` crate. `gpui-wgpu` itself usually checks without
  them. Install the packages listed in `sync.yml` if the main crate fails.
- **DeepSeek key**: `DEEPSEEK_API_KEY`; create one at
  <https://platform.deepseek.com/api_keys>. The agent refuses to run without
  it unless `--dry-run` is given.
