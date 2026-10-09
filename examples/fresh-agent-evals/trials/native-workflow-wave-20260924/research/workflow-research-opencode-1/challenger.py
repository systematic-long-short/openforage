"""Coverage-oriented proposal scheduler for the synthetic research fixture."""

import random


def challenger(ctx):
    """Interleave eligible rotation-feature streams in seeded round-robin order."""
    pair_ids = sorted(ctx.eligible_pairs)
    if not pair_ids:
        return

    random.Random(ctx.seed).shuffle(pair_ids)
    active = [
        (pair_id, iter(ctx.stream_for(pair_id)))
        for pair_id in pair_ids
    ]

    while active:
        next_round = []
        for pair_id, stream in active:
            try:
                payload = next(stream)
            except StopIteration:
                continue
            yield payload
            next_round.append((pair_id, stream))
        active = next_round
