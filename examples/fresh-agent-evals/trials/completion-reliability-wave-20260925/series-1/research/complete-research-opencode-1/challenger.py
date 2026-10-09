"""Coverage-oriented callback for the paper-backed synthetic fixture."""

import json


def _identity_bytes(payload):
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
    return encoded if len(encoded) <= 65_536 else None


def challenger(ctx):
    """Yield a round-robin sweep of unique payloads from eligible cells."""
    pair_ids = sorted(set(ctx.eligible_pairs))
    if not pair_ids:
        return

    # Rotate the first cell by the paired seed so a short final sweep does not
    # always privilege the same feature/rotation pair across runs.
    offset = ctx.seed % len(pair_ids)
    pair_ids = pair_ids[offset:] + pair_ids[:offset]

    streams = []
    for pair_id in pair_ids:
        try:
            streams.append(iter(ctx.stream_for(pair_id)))
        except (KeyError, TypeError):
            continue

    seen = set()
    while streams:
        active = []
        for stream in streams:
            try:
                payload = next(stream)
            except StopIteration:
                continue
            active.append(stream)

            identity = _identity_bytes(payload)
            if identity is not None:
                if identity in seen:
                    continue
                seen.add(identity)

            yield payload

        streams = active
