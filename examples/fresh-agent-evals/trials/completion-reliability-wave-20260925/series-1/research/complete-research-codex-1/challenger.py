"""Descriptor-balanced exploration of the documented synthetic fixture streams.

Inspired by MAP-Elites' descriptor map, without claiming elite selection:
the context supplies neither quality feedback nor candidate depth metadata.
The trusted evaluator gates known identities and depth before evaluation.
"""

import json
import random
from collections import deque


def challenger(ctx):
    """Yield distinct JSON objects fairly across loaded, eligible pair streams."""
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
        # One stream draw per turn, including duplicates, avoids monopolizing
        # proposal opportunities with a noisy descriptor cell.
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
        # Preserve the fixture object: do not fabricate depth or quality labels.
        yield payload
