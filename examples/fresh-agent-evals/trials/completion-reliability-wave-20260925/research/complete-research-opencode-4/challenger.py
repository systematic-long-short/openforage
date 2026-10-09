"""Coverage-first callback for the synthetic research fixture."""

import json


def _canonical_payload(payload):
    """Return a stable key for a bounded JSON payload, or None if invalid."""
    try:
        encoded = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, RecursionError, UnicodeError):
        return None
    if len(encoded) > 65_536:
        return None
    return encoded


def challenger(ctx):
    """Yield candidates breadth-first across eligible rotation-feature cells."""
    descriptors = getattr(ctx, "pair_descriptors", {})
    pair_ids = sorted(
        set(ctx.eligible_pairs),
        key=lambda pair_id: (
            descriptors.get(pair_id, {}).get("rotation", ""),
            descriptors.get(pair_id, {}).get("feature", ""),
            pair_id,
        ),
    )
    if not pair_ids:
        return

    # Rotate the stable descriptor order by the paired seed so a yield cap does
    # not always give the same leading cells the extra proposal opportunity.
    offset = ctx.seed % len(pair_ids)
    pair_ids = pair_ids[offset:] + pair_ids[:offset]

    active_streams = []
    for pair_id in pair_ids:
        try:
            active_streams.append(iter(ctx.stream_for(pair_id)))
        except KeyError:
            # An eligible descriptor without a candidate stream has nothing to
            # propose; it should not prevent traversal of the remaining cells.
            continue

    seen_payloads = set()
    while active_streams:
        next_round = []
        for stream in active_streams:
            try:
                payload = next(stream)
            except StopIteration:
                continue

            next_round.append(stream)
            identity = _canonical_payload(payload)
            if identity is not None:
                if identity in seen_payloads:
                    continue
                seen_payloads.add(identity)
            yield payload

        active_streams = next_round
