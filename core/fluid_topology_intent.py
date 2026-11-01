# Path: core/fluid_topology_intent.py | Purpose: Bind declared fluid segments and attachment intents to optimizer artifacts.
"""Normalize planner intent; no caller declaration establishes a world connection."""

from collections.abc import Mapping
import math


def _point(value):
    if (not isinstance(value, (tuple, list)) or len(value) != 2
            or any(type(v) is not int or abs(v) > 1_000_000 for v in value)):
        raise ValueError("fluid segment tile must contain two bounded integers")
    return list(value)


def _position(value):
    if (not isinstance(value, Mapping) or set(value) != {"x", "y"}
            or any(isinstance(v, bool) or not isinstance(v, (int, float)) or abs(v) > 1_000_000
                   or not math.isfinite(v) for v in value.values())):
        raise ValueError("attachment position must contain finite bounded x and y")
    return {"x": value["x"], "y": value["y"]}


def fluid_topology_intent(plan, segments, permitted_attachments=()):
    """Return detached, JSON-safe intent tied to named planned ghost positions."""
    if (not isinstance(plan, Mapping) or not isinstance(plan.get("phases"), list)
            or not isinstance(segments, (list, tuple)) or len(segments) > 20000
            or not isinstance(permitted_attachments, (list, tuple)) or len(permitted_attachments) > 20000):
        raise ValueError("invalid fluid topology intent")
    normalized = []
    declared = {}
    points = 0
    for segment in segments:
        if (not isinstance(segment, Mapping) or set(segment) - {"fluid", "tiles", "tunnel_endpoints", "separated_by_pump"}
                or not isinstance(segment.get("fluid"), str) or not segment["fluid"]
                or not isinstance(segment.get("tiles"), (list, tuple)) or not segment["tiles"]
                or not isinstance(segment.get("tunnel_endpoints", ()), (list, tuple))
                or type(segment.get("separated_by_pump", False)) is not bool):
            raise ValueError("invalid declared fluid segment")
        tiles = [_point(tile) for tile in segment["tiles"]]
        points += len(tiles)
        if points > 200000:
            raise ValueError("fluid topology intent exceeds tile limit")
        tile_set = {tuple(tile) for tile in tiles}
        pairs = []
        for pair in segment.get("tunnel_endpoints", ()):
            if not isinstance(pair, (tuple, list)) or len(pair) != 2:
                raise ValueError("invalid intended underground pair")
            start, end = _point(pair[0]), _point(pair[1])
            if start == end or tuple(start) not in tile_set or tuple(end) not in tile_set:
                raise ValueError("underground pair must name two distinct segment tiles")
            pairs.append([start, end])
        for tile in tile_set:
            declared.setdefault(tile, set()).add(segment["fluid"])
        normalized.append({"fluid": segment["fluid"], "tiles": tiles, "tunnel_endpoints": pairs,
                           "separated_by_pump": segment.get("separated_by_pump", False)})
    planned = set()
    for phase in plan["phases"]:
        if not isinstance(phase, Mapping) or not isinstance(phase.get("actions"), list):
            raise ValueError("invalid fluid intent plan")
        for action in phase["actions"]:
            if not isinstance(action, Mapping) or action.get("action_type") != "place_ghost":
                raise ValueError("fluid intent requires entity ghost actions")
            position = _position(action.get("position"))
            name = action.get("entity")
            if not isinstance(name, str) or not name:
                raise ValueError("fluid intent requires named entities")
            planned.add((name, position["x"], position["y"]))
    occupied_tiles = {(math.floor(x), math.floor(y)) for _, x, y in planned}
    if not set(declared).issubset(occupied_tiles):
        raise ValueError("declared fluid tile has no planned entity")
    permits = []
    seen = set()
    for permit in permitted_attachments:
        if (not isinstance(permit, Mapping) or set(permit) != {"planned", "existing", "fluid"}
                or not isinstance(permit["fluid"], str) or not permit["fluid"]):
            raise ValueError("invalid permitted fluid attachment")
        planned_end, existing = permit["planned"], permit["existing"]
        if (not isinstance(planned_end, Mapping) or set(planned_end) != {"entity", "position"}
                or not isinstance(existing, Mapping) or set(existing) != {"entity", "position", "unit_number"}
                or any(not isinstance(end.get("entity"), str) or not end["entity"] for end in (planned_end, existing))
                or type(existing["unit_number"]) is not int or existing["unit_number"] <= 0):
            raise ValueError("invalid permitted fluid attachment identity")
        p, e = _position(planned_end["position"]), _position(existing["position"])
        if ((planned_end["entity"], p["x"], p["y"]) not in planned
                or permit["fluid"] not in declared.get((math.floor(p["x"]), math.floor(p["y"])), set())):
            raise ValueError("permitted attachment does not name a declared planned fluid entity")
        key = (planned_end["entity"], p["x"], p["y"], existing["entity"], e["x"], e["y"], existing["unit_number"])
        if key in seen:
            raise ValueError("duplicate permitted fluid attachment")
        seen.add(key)
        permits.append({"planned": {"entity": planned_end["entity"], "position": p},
                        "existing": {"entity": existing["entity"], "position": e,
                                     "unit_number": existing["unit_number"]}, "fluid": permit["fluid"]})
    return {"version": "fluid-topology-intent-v1", "segments": normalized, "permitted_attachments": permits}
