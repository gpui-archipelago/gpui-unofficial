# Local CI fix helpers

These scripts run the former `gh-aw` CI Fix agent without GitHub Actions, a
GitHub MCP server, or `ANTHROPIC_API_KEY`. The agent-facing instructions live in
[`.agents/skills/ci-fix-local/SKILL.md`](../../.agents/skills/ci-fix-local/SKILL.md).

## Commands

```sh
# Collect the latest failed Sync run into a compact context bundle
scripts/ci-fix/collect.sh --latest
scripts/ci-fix/collect.sh --run 39684649841
scripts/ci-fix/collect.sh --issue 343

# Reproduce with a shallow zed clone + transform + one cargo check
scripts/ci-fix/reproduce.sh --run <run-id>
scripts/ci-fix/reproduce.sh --tag v1.25.2 --target gpui-wgpu
scripts/ci-fix/reproduce.sh --run <run-id> --all --isolated   # full host matrix

# Cheap verification (xtask tests, script syntax, targeted repro)
scripts/ci-fix/verify.sh --run <run-id>
scripts/ci-fix/verify.sh --run <run-id> --full

# Open a draft PR (dry run unless --push)
scripts/ci-fix/submit.sh --run <run-id>
scripts/ci-fix/submit.sh --run <run-id> --push

# Cost estimates (DeepSeek prices captured 2026-10-22)
scripts/ci-fix/estimate-cost.sh --scenarios

# Optional headless DeepSeek agent (requires DEEPSEEK_API_KEY)
python3 scripts/ci-fix/deepseek_agent.py --run <run-id>
python3 scripts/ci-fix/deepseek_agent.py --run <run-id> --dry-run
```

## Layout

| file | purpose |
| --- | --- |
| `lib.sh` | repo root, cache, crate-name mapping, zed checkout helper |
| `collect.sh` | `gh`-based failure collection and context bundling |
| `filter_logs.py` | strips timestamps/ANSI, keeps error lines with context |
| `build_context.py` | writes `context.md` for the model |
| `reproduce.sh` | transform + targeted `cargo check` / isolated packaging |
| `verify.sh` | change-aware verification suite |
| `submit.sh` | branch/commit/push/draft PR, with duplicate-PR guard |
| `cost.py` | pricing, scenarios, usage-file accounting |
| `deepseek_agent.py` | budgeted stdlib tool loop for `deepseek-flash` |
| `prompts/ci-fix-system.md` | system prompt for the headless loop |

All generated state lives under `target/ci-fix/`, which is gitignored.
