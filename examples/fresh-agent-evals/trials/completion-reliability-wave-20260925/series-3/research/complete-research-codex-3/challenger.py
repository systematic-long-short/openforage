"""Descriptor-balanced proposals for the test-only finite fixture context.

This is coverage scheduling inspired by MAP-Elites, not elite selection:
the context supplies no evaluation feedback or candidate quality labels.
The trusted evaluator owns eligibility and depth validation of opaque payloads.
"""

import json
import random
from collections import deque


def challenger(ctx):
    """Interleave eligible cells, deduplicate, and terminate on exhaustion."""
    pairs = sorted(set(ctx.eligible_pairs) - set(ctx.excluded_pairs))
    random.Random(ctx.seed).shuffle(pairs)
    active = deque((pair, ctx.stream_for(pair)) for pair in pairs)
    seen = set()
    while active:
        pair, stream = active.popleft()
        for payload in stream:
            if not isinstance(payload, dict):
                continue
            # Honor an explicit payload depth if present; do not invent one
            # for opaque graphs. Canonical fixture validation remains external.
            if "depth" in payload:
                depth = payload["depth"]
                if type(depth) is not int or not 1 <= depth <= min(6, ctx.depth):
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
            active.append((pair, stream))
            yield payload
            break
