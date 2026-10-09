"""Seeded cell sweeps over finite, test-only candidate streams.

Inspired by MAP-Elites' descriptor map, without quality feedback or mutation.
The independent fixture evaluator owns eligibility, depth and completion checks.
"""

import json
import random
from collections import deque


def challenger(ctx):
    """Yield unchanged proposals, giving each active cell one turn per sweep."""
    pairs = sorted(set(ctx.eligible_pairs) - set(ctx.excluded_pairs))
    random.Random(ctx.seed).shuffle(pairs)
    active = deque(iter(ctx.stream_for(pair)) for pair in pairs)
    seen = set()
    while active:
        stream = active.popleft()
        try:
            payload = next(stream)
        except StopIteration:
            continue
        active.append(stream)
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
        # No candidate metadata is exposed by this context. These are proposals;
        # only the trusted evaluator may count depth-1-through-6 completions.
        yield payload
