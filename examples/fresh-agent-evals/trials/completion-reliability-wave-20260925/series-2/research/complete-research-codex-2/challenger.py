"""Descriptor-balanced proposals for the documented synthetic fixture only.

Inspired by Mouret and Clune (2015), arXiv:1504.04909. This uses descriptor
cells for allocation, not a quality archive: the context has no feedback.
The evaluator owns identity, eligibility and depth validation (cap six).
"""

import json
import random
from collections import deque


def challenger(ctx):
    """Visit each live eligible cell once per sweep; retire exhausted streams."""
    pairs = sorted(set(ctx.eligible_pairs) - set(ctx.excluded_pairs))
    random.Random(ctx.seed).shuffle(pairs)
    active = deque((pair, iter(ctx.stream_for(pair))) for pair in pairs)
    seen = set()

    while active:
        pair, stream = active.popleft()
        try:
            payload = next(stream)
        except StopIteration:
            continue
        active.append((pair, stream))
        if not isinstance(payload, dict):
            continue
        try:
            identity = json.dumps(
                payload, sort_keys=True, separators=(",", ":"),
                ensure_ascii=False, allow_nan=False,
            )
        except (TypeError, ValueError, RecursionError, UnicodeEncodeError):
            continue
        if identity in seen:
            continue
        seen.add(identity)
        # Forward unchanged proposals. No private depth/quality metadata is
        # available here; only the trusted evaluator can count completions.
        yield payload
