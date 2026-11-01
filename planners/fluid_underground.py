# Path: planners/fluid_underground.py | Purpose: Check intended underground-pipe partners in an offline candidate.
"""Conservative planned-pipe pairing checks; no world or runtime claim."""
from __future__ import annotations

import math

from core.fluid_systems import validate_underground_span
from planners.plan_validation import actions

_DIRECTIONS = {"north": (0, -1), "east": (1, 0),
               "south": (0, 1), "west": (-1, 0)}


def validate_planned_underground_pairs(plan: dict, segments: list[dict]) -> None:
    """Require explicit opposite-facing partners without nearer competitors.

    Normal ends face away from the intended underground span. This deliberately
    rejects another opposing endpoint nearer than the intended partner. More
    distant opposing endpoints may exist. It does not inspect a world, prove
    engine pairing rules or validate other prototype-specific connection ranges.
    """
    endpoints = {}
    for action in actions(plan):
        if action.get("action_type") != "place_ghost" or action.get("entity") != "pipe-to-ground":
            continue
        position = action["position"]
        tile = (math.floor(position["x"]), math.floor(position["y"]))
        if tile in endpoints or action.get("direction") not in _DIRECTIONS:
            raise ValueError(f"duplicate or invalid underground endpoint at {tile}")
        endpoints[tile] = _DIRECTIONS[action["direction"]]

    partners = {}
    for segment in segments:
        for pair in segment.get("tunnel_endpoints", ()):
            if len(pair) != 2:
                raise ValueError("underground pair must have two endpoints")
            first, second = tuple(pair[0]), tuple(pair[1])
            validate_underground_span(first, second)
            if first == second or first not in endpoints or second not in endpoints:
                raise ValueError("underground pair has missing or coincident endpoints")
            if first in partners or second in partners:
                raise ValueError("underground endpoint belongs to multiple pairs")
            delta = (second[0] - first[0], second[1] - first[1])
            step = (0 if delta[0] == 0 else delta[0] // abs(delta[0]),
                    0 if delta[1] == 0 else delta[1] // abs(delta[1]))
            if endpoints[first] != (-step[0], -step[1]) or endpoints[second] != step:
                raise ValueError("underground endpoints do not face away from their intended pair")
            partners[first], partners[second] = second, first
    if set(endpoints) != set(partners):
        raise ValueError("underground endpoint lacks an intended partner")

    for tile, normal in endpoints.items():
        partner = partners[tile]
        intended_distance = abs(tile[0] - partner[0]) + abs(tile[1] - partner[1])
        for distance in range(1, intended_distance):
            candidate = (tile[0] - normal[0] * distance, tile[1] - normal[1] * distance)
            if (candidate in endpoints and endpoints[candidate] == (-normal[0], -normal[1])
                    and candidate != partners[tile]):
                raise ValueError(f"ambiguous underground partners at {tile} and {candidate}")
