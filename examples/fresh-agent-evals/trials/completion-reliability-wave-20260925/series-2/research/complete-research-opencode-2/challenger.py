"""Round-robin construction coverage over the synthetic fixture's cells."""


def challenger(ctx):
    """Yield one proposal per eligible feature/rotation cell per round."""
    eligible = tuple(
        sorted(
            ctx.eligible_pairs,
            key=lambda pair_id: (
                ctx.pair_descriptors[pair_id]["rotation"],
                ctx.pair_descriptors[pair_id]["feature"],
                pair_id,
            ),
        )
    )
    if not eligible:
        return

    # Rotate the first cell by seed so a finite call budget does not always
    # privilege the same lexicographically earliest descriptor.
    offset = ctx.seed % len(eligible)
    order = eligible[offset:] + eligible[:offset]
    streams = [iter(ctx.stream_for(pair_id)) for pair_id in order]

    # Advance every eligible cell once before taking another proposal from any
    # cell. The evaluator independently validates each yielded payload.
    while streams:
        next_round = []
        for stream in streams:
            try:
                payload = next(stream)
            except StopIteration:
                continue
            next_round.append(stream)
            yield payload
        streams = next_round
