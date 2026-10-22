#!/usr/bin/env python3
"""A small, budgeted DeepSeek agent that drives the local CI-fix workflow.

This is intentionally dependency-free (stdlib only) and deliberately narrow:
it can read/search/write repository files and run a small allow-list of
commands. It is not a general coding agent.

Usage:
    # requires DEEPSEEK_API_KEY
    python3 scripts/ci-fix/deepseek_agent.py --run 39684649841
    python3 scripts/ci-fix/deepseek_agent.py --context target/ci-fix/39684649841/context.md
    python3 scripts/ci-fix/deepseek_agent.py --run 39684649841 --dry-run

The agent never commits or pushes. Review its diff, run verify.sh, then use
submit.sh.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Optional

HERE = Path(__file__).resolve().parent
sys.dont_write_bytecode = True
sys.path.insert(0, str(HERE))
import cost  # noqa: E402

DEFAULT_MODEL = "deepseek-flash"
DEFAULT_BASE_URL = "https://api.deepseek.com"
MAX_READ_CHARS = 48_000
MAX_TOOL_OUTPUT = 24_000
MAX_CONTEXT_CHARS = 120_000
RETRYABLE_HTTP = {408, 409, 429, 500, 502, 503, 504}


class ToolError(Exception):
    pass


def find_repo_root(explicit: Optional[str]) -> Path:
    if explicit:
        return Path(explicit).resolve()
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--show-toplevel"], text=True, stderr=subprocess.DEVNULL
        ).strip()
        if out:
            return Path(out).resolve()
    except (OSError, subprocess.SubprocessError):
        pass
    return HERE.parents[1]


def truncate(text: str, limit: int = MAX_TOOL_OUTPUT) -> str:
    if len(text) <= limit:
        return text
    head = text[: limit // 4]
    tail = text[-(limit * 3 // 4) :]
    return f"{head}\n... [{len(text) - len(head) - len(tail):,} chars omitted] ...\n{tail}"


TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a UTF-8 file in the repository. Use start_line/end_line for targeted reads.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Repository-relative path"},
                    "start_line": {"type": "integer", "description": "1-based first line (optional)"},
                    "end_line": {"type": "integer", "description": "1-based last line, inclusive (optional)"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_dir",
            "description": "List a directory in the repository.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string", "description": "Repository-relative path (default: .)"}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search",
            "description": "Regex search file contents. Prefer this to reading whole files.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Rust-regex/PCRE pattern"},
                    "path": {"type": "string", "description": "Directory or file to search (default: .)"},
                    "glob": {"type": "string", "description": "Optional glob filter, e.g. **/*.rs"},
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write a complete file in the repository. Refuses crates/, .git/, and target/.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Repository-relative path"},
                    "content": {"type": "string", "description": "Full new file contents"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": "Run one read-only/build command in the repository. No shell pipelines or redirection.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Command and arguments, e.g. 'cargo test -p xtask'"},
                    "timeout_seconds": {"type": "integer", "description": "Timeout (default 300, max 900)"},
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "finish",
            "description": "Stop and summarize the work. Call exactly once when done.",
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {
                        "type": "string",
                        "description": "Root cause, files changed, verification commands and results, follow-ups",
                    }
                },
                "required": ["summary"],
            },
        },
    },
]

ALLOWED_BINARIES = {
    "cargo",
    "git",
    "gh",
    "rg",
    "grep",
    "find",
    "ls",
    "cat",
    "head",
    "tail",
    "wc",
    "awk",
    "sed",
    "jq",
    "diff",
    "sort",
    "uniq",
    "cut",
    "tr",
    "python3",
    "bash",
    "sh",
}

CARGO_SUBCOMMANDS = {"check", "build", "test", "tree", "metadata", "clippy", "fmt", "run"}

GIT_SUBCOMMANDS = {
    "status",
    "diff",
    "log",
    "show",
    "grep",
    "ls-files",
    "rev-parse",
    "blame",
    "cat-file",
    "describe",
    "remote",
}

GH_SUBCOMMANDS = {
    "run": {"view", "list"},
    "issue": {"view", "list"},
    "pr": {"view", "list", "diff", "checks", "status"},
    "release": {"view", "list"},
    "api": None,
}


class Agent:
    def __init__(self, repo_root: Path) -> None:
        self.repo_root = repo_root
        self.finished = False
        self.summary = ""
        self.changed_files: list[str] = []

    # -- path / command safety ------------------------------------------------

    def resolve_path(self, value: str, write: bool = False) -> Path:
        if not value:
            raise ToolError("path is required")
        candidate = Path(value)
        if candidate.is_absolute():
            resolved = candidate.resolve()
        else:
            resolved = (self.repo_root / candidate).resolve()

        try:
            rel = resolved.relative_to(self.repo_root)
        except ValueError as exc:
            raise ToolError(f"path is outside the repository: {value}") from exc

        if write and rel.parts and rel.parts[0] in {"crates", ".git", "target"}:
            raise ToolError(f"refusing to write protected/generated path: {rel}")
        return resolved

    def rel(self, path: Path) -> str:
        return str(path.relative_to(self.repo_root))

    def validate_helper_script(self, script: str) -> None:
        resolved = (self.repo_root / script).resolve()
        try:
            resolved.relative_to(HERE)
        except ValueError as exc:
            raise ToolError(f"helper script is outside scripts/ci-fix: {script}") from exc

    def validate_command(self, argv: list[str]) -> None:
        if not argv:
            raise ToolError("empty command")

        for token in argv:
            if token in {">", ">>", "<", "|", "||", "&&", ";", "&"}:
                raise ToolError("shell operators and pipelines are not allowed; run one command")
            if "`" in token or "$(" in token:
                raise ToolError("shell substitution is not allowed")

        # Directly executable helpers (the prompt tells the agent to call these).
        if argv[0].startswith(("scripts/ci-fix/", "./scripts/ci-fix/")):
            self.validate_helper_script(argv[0])
            return

        name = os.path.basename(argv[0])
        if name not in ALLOWED_BINARIES:
            raise ToolError(f"command not allowed: {name}")

        if name == "cargo":
            if len(argv) < 2 or argv[1] not in CARGO_SUBCOMMANDS:
                raise ToolError(f"cargo subcommand not allowed: {' '.join(argv[1:2])}")
            if argv[1] == "run":
                if "xtask" not in argv or not ({"-p", "--package"} & set(argv)):
                    raise ToolError("only 'cargo run -p xtask ...' is allowed")
        elif name == "git":
            sub = argv[1] if len(argv) > 1 else ""
            if sub not in GIT_SUBCOMMANDS:
                raise ToolError(f"git subcommand not allowed: {sub}")
            if sub == "remote" and not ({"-v", "--verbose", "get-url"} & set(argv[2:])):
                raise ToolError("only read-only 'git remote -v' / 'git remote get-url' are allowed")
        elif name == "gh":
            sub = argv[1] if len(argv) > 1 else ""
            if sub not in GH_SUBCOMMANDS:
                raise ToolError(f"gh subcommand not allowed: {sub}")
            if sub == "api":
                forbidden = {"-X", "--method", "-f", "--field", "-F", "--raw-field", "--input"}
                if any(arg in forbidden or arg.startswith("--method=") for arg in argv[2:]):
                    raise ToolError("only read-only (GET) 'gh api' calls are allowed")
            else:
                allowed_actions = GH_SUBCOMMANDS[sub] or set()
                action = argv[2] if len(argv) > 2 else ""
                if action not in allowed_actions:
                    raise ToolError(f"gh {sub} {action} is not allowed")
        elif name in {"bash", "sh", "python3"}:
            if len(argv) < 2:
                raise ToolError(f"{name} needs a script argument")
            self.validate_helper_script(argv[1])
        elif name == "sed":
            if any(arg in {"-i", "--in-place"} or arg.startswith("-i") for arg in argv[1:]):
                raise ToolError("in-place sed is not allowed; use write_file")
        elif name == "find":
            if any(arg in {"-delete", "-exec", "-execdir", "-ok"} for arg in argv[1:]):
                raise ToolError("destructive find actions are not allowed")

    def tool_env(self) -> dict[str, str]:
        keep = {
            "PATH",
            "HOME",
            "USER",
            "LOGNAME",
            "LANG",
            "LC_ALL",
            "TERM",
            "TMPDIR",
            "CARGO_HOME",
            "RUSTUP_HOME",
            "RUSTFLAGS",
            "CC",
            "CXX",
            "PKG_CONFIG_PATH",
            "SSH_AUTH_SOCK",
            "CARGO_TARGET_DIR",
            # Network access in sandboxed sessions goes through the proxy.
            "HTTP_PROXY",
            "HTTPS_PROXY",
            "ALL_PROXY",
            "NO_PROXY",
            "http_proxy",
            "https_proxy",
            "all_proxy",
            "no_proxy",
            "SSL_CERT_FILE",
            "SSL_CERT_DIR",
            # Read-only gh calls the agent may make; not useful for writes.
            "GH_TOKEN",
            "GITHUB_TOKEN",
        }
        env = {key: value for key, value in os.environ.items() if key in keep}
        env.update(
            {
                "CI": "true",
                "CARGO_TERM_COLOR": "never",
                "GH_PROMPT_DISABLED": "1",
                "GIT_TERMINAL_PROMPT": "0",
                "NO_COLOR": "1",
            }
        )
        return env

    # -- tools ----------------------------------------------------------------

    def tool_read_file(self, args: dict[str, Any]) -> str:
        path = self.resolve_path(str(args.get("path", "")))
        if not path.is_file():
            raise ToolError(f"not a file: {path}")
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise ToolError(f"not a UTF-8 text file: {path}") from exc
        except OSError as exc:
            raise ToolError(str(exc)) from exc

        lines = text.splitlines()
        total = len(lines)
        start = int(args.get("start_line") or 1)
        end = int(args.get("end_line") or 0)
        start = max(1, start)
        if end <= 0 or end > total:
            end = total
        if end < start:
            raise ToolError(f"end_line {end} is before start_line {start}")

        clipped = False
        if end - start + 1 > 2000:
            end = start + 1999
            clipped = True
        body = "\n".join(lines[start - 1 : end])
        if len(body) > MAX_READ_CHARS:
            body = body[:MAX_READ_CHARS]
            clipped = True

        header = f"# {self.rel(path)}\n# lines {start}-{end} of {total}"
        if clipped:
            header += f" (clipped; re-read a smaller range for the rest)"
        return f"{header}\n{body}"

    def tool_list_dir(self, args: dict[str, Any]) -> str:
        path = self.resolve_path(str(args.get("path") or "."))
        if path.is_file():
            stat = path.stat()
            return f"file {self.rel(path)} ({stat.st_size:,} bytes)"
        if not path.is_dir():
            raise ToolError(f"not a directory: {path}")

        entries = sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        lines = []
        for entry in entries[:300]:
            if entry.is_dir():
                lines.append(f"d  {entry.name}/")
            else:
                try:
                    size = entry.stat().st_size
                    lines.append(f"f  {size:>12,}  {entry.name}")
                except OSError:
                    lines.append(f"f  {'?':>12}  {entry.name}")
        if len(entries) > 300:
            lines.append(f"... {len(entries) - 300} more entries")
        return "\n".join(lines) or "(empty)"

    def tool_search(self, args: dict[str, Any]) -> str:
        pattern = str(args.get("pattern") or "")
        if not pattern:
            raise ToolError("pattern is required")
        path = self.resolve_path(str(args.get("path") or "."))
        glob = args.get("glob")

        rg = shutil.which("rg")
        if rg:
            cmd = [
                rg,
                "--line-number",
                "--no-heading",
                "--color",
                "never",
                "--hidden",
                "--glob",
                "!.git/**",
                "--glob",
                "!target/**",
                "--max-count",
                "10",
            ]
            if glob:
                cmd += ["--glob", str(glob)]
            cmd += ["--", pattern, str(path)]
            try:
                proc = subprocess.run(
                    cmd, cwd=self.repo_root, capture_output=True, text=True, timeout=30
                )
            except subprocess.TimeoutExpired as exc:
                raise ToolError("search timed out") from exc
            if proc.returncode not in (0, 1):
                raise ToolError(proc.stderr.strip()[:2000] or "rg failed")
            output = proc.stdout
        else:
            output = self._python_search(path, pattern, glob)

        lines = output.splitlines()
        if len(lines) > 150:
            lines = lines[:150] + [f"... {len(lines) - 150} more matches"]
        return "\n".join(lines) or "(no matches)"

    def _python_search(self, path: Path, pattern: str, glob: Optional[str]) -> str:
        try:
            regex = re.compile(pattern)
        except re.error as exc:
            raise ToolError(f"invalid regex: {exc}") from exc

        results: list[str] = []
        for dirpath, dirnames, filenames in os.walk(path):
            dirnames[:] = [d for d in dirnames if d not in {".git", "target", "node_modules"}]
            for filename in filenames:
                candidate = Path(dirpath) / filename
                rel = candidate.relative_to(self.repo_root)
                if glob and not fnmatch.fnmatch(str(rel), str(glob)):
                    continue
                try:
                    if candidate.stat().st_size > 1_000_000:
                        continue
                    text = candidate.read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    continue
                for lineno, line in enumerate(text.splitlines(), 1):
                    if regex.search(line):
                        results.append(f"{rel}:{lineno}: {line.strip()[:220]}")
                        if len(results) >= 120:
                            return "\n".join(results)
        return "\n".join(results)

    def tool_write_file(self, args: dict[str, Any]) -> str:
        path = self.resolve_path(str(args.get("path", "")), write=True)
        content = args.get("content")
        if not isinstance(content, str):
            raise ToolError("content must be a string")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        rel = self.rel(path)
        if rel not in self.changed_files:
            self.changed_files.append(rel)
        return f"wrote {len(content):,} bytes to {rel}"

    def tool_run_command(self, args: dict[str, Any]) -> str:
        command = str(args.get("command") or "").strip()
        if not command:
            raise ToolError("command is required")
        try:
            argv = shlex.split(command)
        except ValueError as exc:
            raise ToolError(f"cannot parse command: {exc}") from exc
        self.validate_command(argv)

        try:
            timeout = int(args.get("timeout_seconds") or 300)
        except (TypeError, ValueError):
            timeout = 300
        timeout = min(max(timeout, 10), 900)

        try:
            proc = subprocess.Popen(
                argv,
                cwd=self.repo_root,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                env=self.tool_env(),
                start_new_session=True,
            )
        except OSError as exc:
            raise ToolError(f"failed to start command: {exc}") from exc

        try:
            output, _ = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except OSError:
                proc.kill()
            output, _ = proc.communicate()
            output += f"\n[killed after {timeout}s]"

        output = truncate(output or "")
        return f"exit={proc.returncode}\n{output or '(no output)'}"

    def dispatch(self, name: str, args: dict[str, Any]) -> tuple[str, bool]:
        if name == "finish":
            self.finished = True
            self.summary = str(args.get("summary") or "").strip()
            return "finished", True

        handlers = {
            "read_file": self.tool_read_file,
            "list_dir": self.tool_list_dir,
            "search": self.tool_search,
            "write_file": self.tool_write_file,
            "run_command": self.tool_run_command,
        }
        handler = handlers.get(name)
        if handler is None:
            return f"ERROR: unknown tool {name!r}", False
        try:
            return handler(args), False
        except ToolError as exc:
            return f"ERROR: {exc}", False
        except Exception as exc:  # keep the loop alive on unexpected tool bugs
            return f"ERROR: {type(exc).__name__}: {exc}", False


class Budget:
    def __init__(self, model: str) -> None:
        self.model = model
        self.total = 0.0
        self.calls: list[dict[str, Any]] = []
        self.prompt_tokens = 0
        self.output_tokens = 0
        self.cache_hit_tokens = 0
        self.cache_miss_tokens = 0

    def add(self, usage: dict[str, Any]) -> cost.Breakdown:
        prompt = int(usage.get("prompt_tokens") or 0)
        completion = int(usage.get("completion_tokens") or 0)
        hit = int(usage.get("prompt_cache_hit_tokens") or 0)
        miss = int(usage.get("prompt_cache_miss_tokens") or 0)
        if miss == 0 and prompt > hit:
            miss = prompt - hit

        breakdown = cost.estimate_usage(prompt, hit, miss, completion, self.model)
        self.total += breakdown.total
        self.prompt_tokens += prompt
        self.output_tokens += completion
        self.cache_hit_tokens += breakdown.cache_hit_tokens
        self.cache_miss_tokens += breakdown.cache_miss_tokens
        self.calls.append(
            {
                "usage": usage,
                "cost_usd": round(breakdown.total, 8),
                "tier": breakdown.tier,
            }
        )
        return breakdown


def call_deepseek(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    args: argparse.Namespace,
    api_key: str,
) -> dict[str, Any]:
    url = args.base_url.rstrip("/") + "/chat/completions"
    payload: dict[str, Any] = {
        "model": args.model,
        "messages": messages,
        "tools": tools,
        "tool_choice": "auto",
        "temperature": args.temperature,
        "max_tokens": args.max_output_tokens,
        "stream": False,
    }
    if args.thinking:
        payload["thinking"] = {"type": "enabled"}
        payload["reasoning_effort"] = args.reasoning_effort

    data = json.dumps(payload).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }

    last_error: Optional[Exception] = None
    for attempt in range(4):
        request = urllib.request.Request(url, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=args.timeout) as response:
                body = response.read().decode("utf-8", errors="replace")
            return json.loads(body)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:1500]
            last_error = RuntimeError(f"DeepSeek API HTTP {exc.code}: {detail}")
            if exc.code in RETRYABLE_HTTP and attempt < 3:
                time.sleep(2**attempt)
                continue
            raise last_error
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = RuntimeError(f"DeepSeek API request failed: {exc}")
            if attempt < 3:
                time.sleep(2**attempt)
                continue
            raise last_error

    raise last_error or RuntimeError("DeepSeek API request failed")


def resolve_context(args: argparse.Namespace, repo_root: Path) -> Path:
    cache = Path(os.environ.get("CI_FIX_CACHE") or (repo_root / "target" / "ci-fix"))
    if args.context:
        return Path(args.context).resolve()
    if args.run:
        return cache / args.run / "context.md"
    return cache / "latest" / "context.md"


def build_messages(context: str, repo_root: Path) -> list[dict[str, Any]]:
    prompt_path = HERE / "prompts" / "ci-fix-system.md"
    system_prompt = prompt_path.read_text(encoding="utf-8") if prompt_path.exists() else (
        "You are a CI-fix agent. Fix the failure, never edit crates/, and call finish when done."
    )
    user_prompt = (
        "Here is the failure context for this run.\n\n"
        f"{context}\n\n"
        "Investigate with the provided tools, make the smallest fix, reproduce and "
        "verify it, then call `finish` with the root cause, changed files, and the "
        "verification commands you ran. Do not commit or push.\n\n"
        f"Repository root: {repo_root}"
    )
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def print_diff(repo_root: Path) -> None:
    try:
        stat = subprocess.run(
            ["git", "diff", "--stat"], cwd=repo_root, capture_output=True, text=True, timeout=15
        )
        if stat.stdout.strip():
            print("\n--- working tree diff ---")
            print(stat.stdout.rstrip())
        else:
            print("\n--- working tree diff ---\n(no tracked changes)")
    except (OSError, subprocess.SubprocessError):
        pass


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--context", help="path to context.md")
    parser.add_argument("--run", help="run id; reads target/ci-fix/<id>/context.md")
    parser.add_argument("--repo-root", help="repository root (default: git toplevel)")
    parser.add_argument("--model", default=DEFAULT_MODEL, choices=sorted(cost.MODELS))
    parser.add_argument("--base-url", default=os.environ.get("DEEPSEEK_BASE_URL", DEFAULT_BASE_URL))
    parser.add_argument("--max-turns", type=int, default=10)
    parser.add_argument("--max-output-tokens", type=int, default=2048)
    parser.add_argument("--max-tool-calls", type=int, default=8, help="per model turn")
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--budget-usd", type=float, default=0.10, help="stop after this estimated spend")
    parser.add_argument("--thinking", action="store_true", help="enable DeepSeek thinking mode")
    parser.add_argument("--reasoning-effort", default="medium", choices=["low", "medium", "high"])
    parser.add_argument("--timeout", type=int, default=180, help="HTTP timeout in seconds")
    parser.add_argument("--usage-out", help="where to write usage/cost JSON")
    parser.add_argument("--dry-run", action="store_true", help="print prompt sizes without calling the API")
    args = parser.parse_args()

    repo_root = find_repo_root(args.repo_root)
    context_path = resolve_context(args, repo_root)
    if not context_path.is_file():
        print(
            f"error: context bundle not found: {context_path}\n"
            "Run scripts/ci-fix/collect.sh first (or pass --context).",
            file=sys.stderr,
        )
        return 2

    context = context_path.read_text(encoding="utf-8", errors="replace")
    if len(context) > MAX_CONTEXT_CHARS:
        context = context[:MAX_CONTEXT_CHARS] + "\n... [context truncated]\n"

    messages = build_messages(context, repo_root)
    prompt_chars = sum(len(message["content"]) for message in messages)

    if args.dry_run:
        print(f"context:      {context_path}")
        print(f"repo root:    {repo_root}")
        print(f"model:        {args.model}")
        print(f"prompt chars: {prompt_chars:,} (~{prompt_chars // 4:,} tokens)")
        print(f"tools:        {', '.join(tool['function']['name'] for tool in TOOL_SCHEMAS)}")
        print(f"max turns:    {args.max_turns}")
        print(f"max output:   {args.max_output_tokens} tokens/turn")
        print(f"budget:       ${args.budget_usd:.2f}")
        typical = cost.estimate(prompt_chars // 4 + 100_000, args.max_turns * args.max_output_tokens // 2, 0.8, args.model)
        print(f"rough cost:   {cost.money(typical.total)} (off-peak/peak depends on the time)")
        return 0

    api_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not api_key:
        print(
            "error: DEEPSEEK_API_KEY is not set.\n"
            "Export it, or run with --dry-run. Key page: https://platform.deepseek.com/api_keys",
            file=sys.stderr,
        )
        return 2

    agent = Agent(repo_root)
    budget = Budget(args.model)
    nudges = 0
    turn = 0
    last_content = ""

    for turn in range(1, args.max_turns + 1):
        print(
            f"[agent] turn {turn}/{args.max_turns} "
            f"(spent {cost.money(budget.total)} of ${args.budget_usd:.2f})",
            file=sys.stderr,
        )
        response = call_deepseek(messages, TOOL_SCHEMAS, args, api_key)

        usage = response.get("usage") or {}
        breakdown = budget.add(usage)
        print(
            f"[agent]   prompt={usage.get('prompt_tokens', 0):,} "
            f"completion={usage.get('completion_tokens', 0):,} "
            f"cost={cost.money(breakdown.total)}",
            file=sys.stderr,
        )

        if budget.total > args.budget_usd:
            print(
                f"[agent] budget exceeded (${budget.total:.4f} > ${args.budget_usd:.2f}); stopping",
                file=sys.stderr,
            )
            break

        choices = response.get("choices") or []
        if not choices:
            print(f"error: API returned no choices: {json.dumps(response)[:1000]}", file=sys.stderr)
            break

        message = choices[0].get("message") or {}
        content = message.get("content") or ""
        last_content = content
        tool_calls = message.get("tool_calls") or []
        tool_calls_to_run = tool_calls[: args.max_tool_calls]

        assistant_message: dict[str, Any] = {"role": "assistant", "content": content}
        if tool_calls_to_run:
            # Keep the assistant message in sync with the tool results we send
            # back; a mismatch makes the next request invalid.
            assistant_message["tool_calls"] = tool_calls_to_run
        if message.get("reasoning_content"):
            assistant_message["reasoning_content"] = message["reasoning_content"]
        messages.append(assistant_message)

        if not tool_calls:
            if agent.finished:
                break
            if nudges < 2:
                nudges += 1
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "You stopped without calling `finish`. Continue investigating with "
                            "the tools, or call `finish` now with your summary."
                        ),
                    }
                )
                continue
            agent.summary = content
            break

        for index, call in enumerate(tool_calls_to_run):
            function = call.get("function") or {}
            name = function.get("name") or ""
            raw_arguments = function.get("arguments") or "{}"
            try:
                tool_args = json.loads(raw_arguments) if isinstance(raw_arguments, str) else raw_arguments
                if not isinstance(tool_args, dict):
                    raise ValueError("arguments must be a JSON object")
            except (json.JSONDecodeError, ValueError) as exc:
                result = f"ERROR: invalid tool arguments: {exc}"
                tool_args = {}
            else:
                result, _ = agent.dispatch(name, tool_args)

            call_id = call.get("id") or f"call_{turn}_{index}"
            messages.append(
                {"role": "tool", "tool_call_id": call_id, "content": truncate(result)}
            )
            if agent.finished:
                break

        if agent.finished:
            break

    # -----------------------------------------------------------------------
    # Report
    # -----------------------------------------------------------------------

    if agent.summary:
        print("\n--- agent summary ---")
        print(agent.summary)
    elif last_content:
        print("\n--- last model message ---")
        print(last_content)
    else:
        print("\n[agent] stopped without a summary", file=sys.stderr)

    if agent.changed_files:
        print("\n--- files written ---")
        for path in agent.changed_files:
            print(f"  {path}")

    print_diff(repo_root)

    usage_path = Path(args.usage_out) if args.usage_out else context_path.parent / "usage.json"
    usage_payload = {
        "model": args.model,
        "turns": turn,
        "finished": agent.finished,
        "summary": agent.summary,
        "changed_files": agent.changed_files,
        "totals": {
            "prompt_tokens": budget.prompt_tokens,
            "completion_tokens": budget.output_tokens,
            "prompt_cache_hit_tokens": budget.cache_hit_tokens,
            "prompt_cache_miss_tokens": budget.cache_miss_tokens,
        },
        "estimated_cost_usd": round(budget.total, 8),
        "calls": budget.calls,
    }
    usage_path.parent.mkdir(parents=True, exist_ok=True)
    usage_path.write_text(json.dumps(usage_payload, indent=2) + "\n", encoding="utf-8")

    print(f"\n[agent] estimated cost: {cost.money(budget.total)}")
    print(f"[agent] usage written to {usage_path}")

    return 0 if agent.finished else 1


if __name__ == "__main__":
    raise SystemExit(main())
