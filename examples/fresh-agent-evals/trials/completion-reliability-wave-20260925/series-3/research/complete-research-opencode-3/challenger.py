"""Coverage-first proposal ordering for the synthetic research fixture."""

from __future__ import annotations

import json


def _canonical_payload(payload: object) -> bytes | None:
    """Return a stable identity for bounded JSON-object payloads."""
    if not isinstance(payload, dict):
        return None
    try:
        encoded = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, RecursionError, UnicodeEncodeError):
        return None
    if len(encoded) > 65_536:
        return None
    return encoded


def challenger(ctx):
    """Yield distinct proposals in rounds across eligible rotation-feature cells.

    The fixture owns graph descriptors and depth validation; this callback
    only schedules its opaque payload streams and removes exact repeats.
    """
    pair_ids = tuple(dict.fromkeys(ctx.eligible_pairs))
    streams = [iter(ctx.stream_for(pair_id)) for pair_id in pair_ids]
    seen_payloads: set[bytes] = set()

    while streams:
        next_round = []
        for stream in streams:
            try:
                payload = next(stream)
            except StopIteration:
                continue

            next_round.append(stream)
            identity = _canonical_payload(payload)
            if identity is None or identity in seen_payloads:
                continue

            seen_payloads.add(identity)
            yield payload

        streams = next_round
