#!/usr/bin/env python3
"""DeepSeek cost estimation for the local CI-fix agent.

Prices are the published DeepSeek rates (USD per 1M tokens) captured on
2026-10-22 from https://api-docs.deepseek.com/quick_start/pricing/.
They can change; update ``MODELS`` rather than editing call sites.

Peak hours are 01:00-04:00 and 06:00-10:00 UTC, Monday-Friday, excluding
Chinese public holidays. We do not model the holiday exceptions.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

MODELS: dict[str, dict[str, dict[str, float]]] = {
    # USD per 1M tokens.
    "deepseek-flash": {
        "cache_hit": {"off_peak": 0.003, "peak": 0.006},
        "cache_miss": {"off_peak": 0.15, "peak": 0.30},
        "output": {"off_peak": 0.60, "peak": 1.20},
    },
    "deepseek-v4-pro": {
        "cache_hit": {"off_peak": 0.022, "peak": 0.044},
        "cache_miss": {"off_peak": 0.66, "peak": 1.32},
        "output": {"off_peak": 1.98, "peak": 3.96},
    },
}

PRICE_CAPTURED = "2026-10-22"


def is_peak(when: Optional[dt.datetime] = None) -> bool:
    when = when or dt.datetime.now(dt.timezone.utc)
    if when.weekday() >= 5:  # Saturday/Sunday
        return False
    hour = when.hour
    return 1 <= hour < 4 or 6 <= hour < 10


@dataclass
class Breakdown:
    model: str
    tier: str
    prompt_tokens: int
    cache_hit_tokens: int
    cache_miss_tokens: int
    output_tokens: int
    input_cost: float
    output_cost: float

    @property
    def total(self) -> float:
        return self.input_cost + self.output_cost

    def as_dict(self) -> dict:
        return {
            "model": self.model,
            "tier": self.tier,
            "prompt_tokens": self.prompt_tokens,
            "cache_hit_tokens": self.cache_hit_tokens,
            "cache_miss_tokens": self.cache_miss_tokens,
            "output_tokens": self.output_tokens,
            "input_cost_usd": round(self.input_cost, 8),
            "output_cost_usd": round(self.output_cost, 8),
            "total_cost_usd": round(self.total, 8),
        }


def estimate(
    input_tokens: int,
    output_tokens: int,
    cache_hit_ratio: float = 0.8,
    model: str = "deepseek-flash",
    peak: Optional[bool] = None,
) -> Breakdown:
    if model not in MODELS:
        raise ValueError(f"unknown model {model!r}; known: {', '.join(MODELS)}")
    cache_hit_ratio = min(1.0, max(0.0, cache_hit_ratio))
    if peak is None:
        peak = is_peak()
    tier = "peak" if peak else "off_peak"
    prices = MODELS[model]

    hit = int(round(input_tokens * cache_hit_ratio))
    miss = max(0, input_tokens - hit)

    input_cost = (
        hit * prices["cache_hit"][tier] + miss * prices["cache_miss"][tier]
    ) / 1_000_000
    output_cost = output_tokens * prices["output"][tier] / 1_000_000

    return Breakdown(
        model=model,
        tier=tier,
        prompt_tokens=input_tokens,
        cache_hit_tokens=hit,
        cache_miss_tokens=miss,
        output_tokens=output_tokens,
        input_cost=input_cost,
        output_cost=output_cost,
    )


def estimate_usage(
    prompt_tokens: int,
    cache_hit_tokens: int,
    cache_miss_tokens: int,
    output_tokens: int,
    model: str = "deepseek-flash",
    peak: Optional[bool] = None,
) -> Breakdown:
    """Cost from the exact hit/miss split reported by the API."""
    if model not in MODELS:
        raise ValueError(f"unknown model {model!r}; known: {', '.join(MODELS)}")
    if peak is None:
        peak = is_peak()
    tier = "peak" if peak else "off_peak"
    prices = MODELS[model]

    cache_hit_tokens = max(0, min(cache_hit_tokens, prompt_tokens))
    cache_miss_tokens = max(cache_miss_tokens, prompt_tokens - cache_hit_tokens)
    input_cost = (
        cache_hit_tokens * prices["cache_hit"][tier]
        + cache_miss_tokens * prices["cache_miss"][tier]
    ) / 1_000_000
    output_cost = output_tokens * prices["output"][tier] / 1_000_000

    return Breakdown(
        model=model,
        tier=tier,
        prompt_tokens=prompt_tokens,
        cache_hit_tokens=cache_hit_tokens,
        cache_miss_tokens=cache_miss_tokens,
        output_tokens=output_tokens,
        input_cost=input_cost,
        output_cost=output_cost,
    )


def money(value: float) -> str:
    if value == 0:
        return "$0"
    if value < 0.0001:
        return f"${value:.7f}"
    return f"${value:.5f}"


def print_scenarios(model: str) -> None:
    scenarios = [
        # name, turns, cumulative prompt tokens, output tokens, cache hit ratio
        ("lean", 8, 60_000, 8_000, 0.80),
        ("typical", 12, 180_000, 12_000, 0.80),
        ("heavy", 20, 600_000, 25_000, 0.85),
    ]
    print(f"Model: {model} (prices captured {PRICE_CAPTURED}, USD)")
    print()
    print(f"{'scenario':<10} {'turns':>5} {'input':>10} {'output':>8} {'off-peak':>10} {'peak':>10}")
    print("-" * 62)
    for name, turns, inp, out, ratio in scenarios:
        off = estimate(inp, out, ratio, model, peak=False)
        peak = estimate(inp, out, ratio, model, peak=True)
        print(
            f"{name:<10} {turns:>5} {inp:>10,} {out:>8,} "
            f"{money(off.total):>10} {money(peak.total):>10}"
        )
    print()
    print("Assumes automatic prompt caching; the agent reuses a stable prefix.")
    print("Tool output is truncated and raw job logs are never sent to the model.")


def usage_totals(path: Path) -> tuple[dict[str, int], Optional[str]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    model = data.get("model")

    calls = data.get("calls")
    if isinstance(calls, list):
        totals = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "prompt_cache_hit_tokens": 0,
            "prompt_cache_miss_tokens": 0,
        }
        for call in calls:
            usage = call.get("usage", call)
            for key in totals:
                totals[key] += int(usage.get(key, 0) or 0)
        return totals, model

    usage = data.get("usage", data)
    totals = {
        "prompt_tokens": int(usage.get("prompt_tokens", usage.get("input_tokens", 0)) or 0),
        "completion_tokens": int(usage.get("completion_tokens", usage.get("output_tokens", 0)) or 0),
        "prompt_cache_hit_tokens": int(usage.get("prompt_cache_hit_tokens", 0) or 0),
        "prompt_cache_miss_tokens": int(usage.get("prompt_cache_miss_tokens", 0) or 0),
    }
    return totals, model


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="deepseek-flash", choices=sorted(MODELS))
    parser.add_argument("--input", type=int, default=0, help="cumulative prompt tokens")
    parser.add_argument("--output", type=int, default=0, help="cumulative completion tokens")
    parser.add_argument("--cache-hit-ratio", type=float, default=0.8)
    parser.add_argument("--peak", action="store_true", help="force peak pricing")
    parser.add_argument("--off-peak", action="store_true", help="force off-peak pricing")
    parser.add_argument("--scenarios", action="store_true", help="print typical run scenarios")
    parser.add_argument("--usage", type=Path, help="read a usage JSON file emitted by the agent")
    parser.add_argument("--json", action="store_true", help="emit JSON")
    args = parser.parse_args()

    if args.peak and args.off_peak:
        parser.error("choose at most one of --peak/--off-peak")

    if args.scenarios:
        print_scenarios(args.model)
        return 0

    peak = True if args.peak else False if args.off_peak else None

    if args.usage:
        totals, model = usage_totals(args.usage)
        model = args.model if args.model != "deepseek-flash" else (model or args.model)
        hit = totals["prompt_cache_hit_tokens"]
        miss = totals["prompt_cache_miss_tokens"]
        prompt = totals["prompt_tokens"] or (hit + miss)
        ratio = (hit / prompt) if prompt else args.cache_hit_ratio
        result = estimate(prompt, totals["completion_tokens"], ratio, model, peak)
    else:
        result = estimate(args.input, args.output, args.cache_hit_ratio, args.model, peak)

    if args.json:
        print(json.dumps(result.as_dict(), indent=2))
    else:
        print(f"model:        {result.model}")
        print(f"pricing:      {result.tier}")
        print(f"input:        {result.prompt_tokens:,} tokens ({result.cache_hit_tokens:,} cache hit / {result.cache_miss_tokens:,} miss)")
        print(f"output:       {result.output_tokens:,} tokens")
        print(f"input cost:   {money(result.input_cost)}")
        print(f"output cost:  {money(result.output_cost)}")
        print(f"total:        {money(result.total)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
