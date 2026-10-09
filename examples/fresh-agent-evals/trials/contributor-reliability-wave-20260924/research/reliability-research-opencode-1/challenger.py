"""Cell-balanced proposal order for the paper-backed synthetic fixture."""

from collections import deque
import random


def challenger(ctx):
    """Yield fixture payloads round-robin across eligible descriptor cells."""
    pairs_by_cell = {}
    for pair_id in ctx.eligible_pairs:
        descriptor = ctx.pair_descriptors.get(pair_id)
        if not isinstance(descriptor, dict):
            continue
        if descriptor.get("loaded") is not True or descriptor.get("eligible") is not True:
            continue

        rotation = descriptor.get("rotation")
        feature = descriptor.get("feature")
        if not isinstance(rotation, str) or not isinstance(feature, str):
            continue
        pairs_by_cell.setdefault((rotation, feature), []).append(pair_id)

    if not pairs_by_cell:
        return

    rng = random.Random(ctx.seed)
    cell_order = sorted(pairs_by_cell)
    rng.shuffle(cell_order)

    streams_by_cell = {}
    active_cells = deque()
    for cell in cell_order:
        pair_ids = list(dict.fromkeys(pairs_by_cell[cell]))
        rng.shuffle(pair_ids)
        streams_by_cell[cell] = deque(
            (pair_id, iter(ctx.stream_for(pair_id))) for pair_id in pair_ids
        )
        active_cells.append(cell)

    while active_cells:
        cell = active_cells.popleft()
        streams = streams_by_cell[cell]
        while streams:
            pair_id, payloads = streams.popleft()
            try:
                payload = next(payloads)
            except StopIteration:
                continue

            streams.append((pair_id, payloads))
            active_cells.append(cell)
            yield payload
            break
        else:
            del streams_by_cell[cell]
