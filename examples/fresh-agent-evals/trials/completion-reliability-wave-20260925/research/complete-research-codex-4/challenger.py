"""Descriptor-balanced proposals for the test-only finite fixture streams.

Inspired by MAP-Elites' descriptor map, without elite selection or fitness
feedback. Only the independent evaluator can establish a valid cell visit.
"""

import json
import random
from collections import deque


def _over_depth(value, cap):
    """Reject any explicitly supplied depth outside the permitted range."""
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "depth":
                if type(child) is not int or not 1 <= child <= cap:
                    return True
            if _over_depth(child, cap):
                return True
    elif isinstance(value, list):
        return any(_over_depth(child, cap) for child in value)
    return False


def challenger(ctx):
    """Yield unique payloads, one per active descriptor cell each round.

    Stream exhaustion removes a cell permanently. Rejected local payloads
    consume stream entries but not a cell's turn. There is no quality lookup,
    evaluator feedback, filesystem access, or invented completion counter.
    """
    pairs = sorted(set(ctx.eligible_pairs) - set(ctx.excluded_pairs))
    pairs = [pair for pair in pairs
             if ctx.pair_descriptors[pair]["loaded"] is True
             and ctx.pair_descriptors[pair]["eligible"] is True]
    random.Random(ctx.seed).shuffle(pairs)
    active = deque(iter(ctx.stream_for(pair)) for pair in pairs)
    seen = set()
    cap = min(6, ctx.depth)
    while active:
        stream = active.popleft()
        for payload in stream:
            if not isinstance(payload, dict) or _over_depth(payload, cap):
                continue
            try:
                identity = json.dumps(payload, sort_keys=True,
                                      separators=(",", ":"), ensure_ascii=False,
                                      allow_nan=False).encode("utf-8")
            except (TypeError, ValueError, RecursionError, UnicodeEncodeError):
                continue
            if len(identity) > 65536 or identity in seen:
                continue
            seen.add(identity)
            active.append(stream)
            yield payload
            break
