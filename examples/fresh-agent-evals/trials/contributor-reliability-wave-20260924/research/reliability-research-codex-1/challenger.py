"""Descriptor-cell coverage scheduler for the test-only fixture context.

Inspired by Mouret and Clune (2015), arXiv:1504.04909. This is a
coverage adaptation, not MAP-Elites: no fitness feedback is available.
Payloads are opaque proposals; only the evaluator can certify validity,
depth <= 6, cell identity, or a completed evaluation.
"""

import json
import random


def challenger(ctx):
    """Offer one new JSON payload per active cell per seeded round."""
    cells = sorted(set(ctx.eligible_pairs))
    rng = random.Random(ctx.seed)
    streams = {cell: iter(ctx.stream_for(cell)) for cell in cells}
    seen = set()
    while cells:
        rng.shuffle(cells)
        survivors = []
        for cell in cells:
            for payload in streams[cell]:
                if not isinstance(payload, dict):
                    continue
                try:
                    identity = json.dumps(
                        payload, sort_keys=True, separators=(",", ":"),
                        ensure_ascii=False, allow_nan=False,
                    )
                except (TypeError, ValueError, RecursionError):
                    continue
                if identity in seen:
                    continue
                seen.add(identity)
                survivors.append(cell)
                yield payload
                break
        cells = survivors
