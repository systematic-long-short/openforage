#!/usr/bin/env python3
"""Read public campaign evidence and print one deterministic next-action proposal.

The command is stdlib-only, read-only, and never launches work. The harness
owns task records, goals, memory, scheduling, and execution.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.dont_write_bytecode = True

from research_next_core import (  # noqa: E402
    NextActionError,
    load_packet,
    render_text,
    run_selfcheck,
)

ROOT = Path(__file__).resolve().parents[1]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign", help="optional existing campaign.json manifest")
    parser.add_argument("--coverage", help="optional existing coverage.jsonl ledger")
    parser.add_argument(
        "--experiments",
        "--experiment",
        dest="experiments",
        help="optional experiment_record.json or experiments.jsonl evidence",
    )
    parser.add_argument("--context", help="optional helper progress/authorization observation JSON")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    parser.add_argument("--selfcheck", action="store_true", help="run deterministic offline decision regressions")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.selfcheck:
        return run_selfcheck(ROOT)
    try:
        packet = load_packet(
            ROOT,
            campaign_path=args.campaign,
            coverage_path=args.coverage,
            experiments_path=args.experiments,
            context_path=args.context,
        )
    except NextActionError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    if args.format == "json":
        print(json.dumps(packet, indent=2, ensure_ascii=False))
    else:
        print(render_text(packet), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
