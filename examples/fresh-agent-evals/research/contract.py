"""Explicit test-only callback context for the synthetic research fixture.

This is not an OpenForage SearchContext or a library API. It supplies stable,
seeded finite streams so an offline evaluator can execute a callback of the
public ``challenger(ctx)`` generator shape.
"""

from __future__ import annotations

import hashlib
import json
import random
from collections.abc import Iterator
from typing import Any


FIXTURE_CONTEXT_INTERFACE = "paper-backed-research-fixture-context-v1"


def _stable_pair_seed(input_id: str, seed: int, pair_id: str) -> int:
    text = f"{FIXTURE_CONTEXT_INTERFACE}\0{input_id}\0{seed}\0{pair_id}"
    return int.from_bytes(hashlib.sha256(text.encode("utf-8")).digest()[:8], "big")


class FixtureContext:
    """Test-only synthetic context passed to the subject callback.

    ``stream_for`` returns one shared iterator per pair id. Repeated calls
    return its current cursor. The descriptor metadata is supplied by the
    fixture and is not graph introspection or a public library contract.
    """

    __fixture_interface__ = FIXTURE_CONTEXT_INTERFACE

    def __init__(self, fixture_input: dict[str, Any], seed: int):
        if type(seed) is not int:
            raise TypeError("fixture seed must be an integer")
        self.seed = seed
        self.depth = 6
        self.scope = {
            "era_id": fixture_input["era_id"],
            "config_id": fixture_input["config_id"],
        }
        pairs = fixture_input["pairs"]
        self.pair_descriptors = {
            item["pair_id"]: {
                "rotation": item["rotation"],
                "feature": item["feature"],
                "loaded": item["loaded"],
                "eligible": item["eligible"],
                "exclusion_reason": item["exclusion_reason"],
            }
            for item in pairs
        }
        self.eligible_pairs = tuple(
            item["pair_id"] for item in pairs
            if item["loaded"] is True and item["eligible"] is True
        )
        self.excluded_pairs = tuple(
            item["pair_id"] for item in pairs
            if item["loaded"] is not True or item["eligible"] is not True
        )

        self._streams: dict[str, Iterator[object]] = {}
        for pair_id, records in fixture_input["candidate_streams"].items():
            payloads = [record["payload"] for record in records]
            random.Random(_stable_pair_seed(fixture_input["input_id"], seed, pair_id)).shuffle(payloads)
            self._streams[pair_id] = iter(payloads)

    def stream_for(self, pair_id: str) -> Iterator[object]:
        """Return the finite synthetic payload stream for one fixture pair."""
        try:
            return self._streams[pair_id]
        except KeyError as exc:
            raise KeyError(f"unknown fixture pair id: {pair_id}") from exc


def canonical_fixture_payload(payload: object, *, max_bytes: int = 65_536) -> tuple[str, bytes] | None:
    """Hash a JSON-object payload without relying on object repr addresses."""
    if not isinstance(payload, dict):
        return None
    try:
        encoded = json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, RecursionError, UnicodeEncodeError):
        return None
    if len(encoded) > max_bytes:
        return None
    return hashlib.sha256(encoded).hexdigest(), encoded
