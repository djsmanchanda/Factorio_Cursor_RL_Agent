# Path: planners/refinery_blueprints.py
# Purpose: Decode and strictly validate the user-approved modular electric-refinery blueprints before they become BuildPlan actions.

from __future__ import annotations

import base64
import hashlib
import json
import zlib
from collections import Counter
from dataclasses import dataclass


REFINERY_START = (
    "0eNq1lu9qwyAUxd/lfjajmv95lTGKbW0npBr0ZqyUvPu0XRnryvDK9iXB3OvP4wnH5AybcVaT0wZhOIM3cirQFgend3H8DgMXDE4wlAsDvbXGw/Ac+vTByDF2GHlUMMBeeizQSeMn67DYqBEhzjA7FRnLCwNlUKNWV8BlcFqb+bhRLjSw30AMJuvDXGs+NRWrp/qiKtzDKjvt1PZarhb2Ay4y4TwFXtLgJOFVHjtJd01jc4ruhsYWFHabx07ypMvzJInd09glxRO++g7306gRQ+UHtrpJvoMysDNOM67DSWBdYAeU04fXqEybh4VHOnjeJpMM5MQY1yQHy1QHm/91kBj5jrRJYuZbErzJU5727ts85WlwauxJZzgnBr8nfdlWmdKTjBE8T3oanBjnW+7EPbx7BE+O8+2UEH8Q5/CPo1Edw4OvfyoGowwbik1qr41yp7VH6WLhTTl/WaxuRF/1fd1VTRsuy/IBRzkwYA=="
)
REFINERY_MIDDLE = (
    "0eNqdmOtuozAQhd/Fv80K37i9ShRFJEwiS2CQcXY3qnj3mnTVVltox/MnCGI+D4c5xxYv7NzfYfLWBda8sNm1UxbG7OZtt57/ZY2QnD1YUy+c2cvoZtYc4jh7c22/jnDtAKxh0MMleHvJrnfv2guwdbjrYAUsfOOGazuHLPjWzdPoQ3aGPny6Ry5HzsAFGyy8zfg8eZzcfTiDj1D+HYizaZzjvaP79xD5L/N8iniMk3TWx2qf/1Zrcf+xZRq7SGGrNLaQKXBNE0Vg2IbGlhh28c62bgYf4rWvSuwB9Qaw5Lt9+QWsPqmwgaoQtZmU2mpad6GEFDkNjuoAIRBSlHvlCrmFlAkvqv7+RQmqtXDKaiIdJ63BOECkaVvQHKtQBZc0uEbBPzw3QGfvQ/beHNPYw3426J3OqBPyRWE8LHNCwOxUJ0VCwuCqIy5gqJcjFQ2OaiupE998+YO2JiGyFMpWsiBE1l55JTFUcGJWRDquD+qUyMJpq/JU4//gLUXcJhaorZykwQ0KrhIyq8CkgtIJOYMjGloU4NQtaHCcumVCLhS43qW6DVdwTaSjtNZ5ipdxemii9SpUwUTrlSi4Iizu5XYAaU3rYpwKRP/hVKCsc3sqUNc5XKVU522IfOTsTzxfPzkc4jbU8LgjMUd+WA88Lk/mGEfYAEOc7OPDCWd9G+eK1zxcrQP/OA2263o4eZigDdbd4pjf4OfnNKaQta5rU+mijD/L8go0rL7H"
)
REFINERY_END = (
    "0eNq1metu4jAQhd/Fv80qvse8SlWhFAyyFJzIMbtbVbz7OuyqVG3CzozUP4SE5Mv44HMmlzf20l/CmGMqbPvGptSNmzJsTjke5vXfbCskZ691Ia6cxf2QJrZ9qjvGU+r6eZfUnQPbstCHfclxvzlecur2gc27p0OYCVe+cMCxm8qm5C5N45DL5iX05cMx8vrMWUgllhj+nvG28rpLl/NLyBXKH4E4G4epHjukf6NofpjbMOqynuQQc6329ms7F/eJLWlsAWErvqrYF7D6AF5AaVyZFlOmobFB8lqEBP6xBA5XppAYDVoiHCSCp80xBWGLhgaXIPjdeTFNIZe68asWa+XqJaJEECWIqGjTF6auBpRrUAIQ3Qb7vyyiXJi6DkB0awIIuYRsEUgJQ3qifUGTQDaQOStQIkhBrBg0D6TEVAzTWCpa0BhQwZoG1yD43XLncIiX8+a9F41DH9bzRy83ImkJfX2N5WhxAFO1pcFhqnqkqu6xEqohXCqssajuAsmqJJEO0lUp7Gz9zxRTRG85ULWGBrcguEVcLFhIO1OOYF23oivRXQ5UqKfBQbrqBnGZANJVC0RPt6B+oyUhD1b+Kq2IjoXJqYl0kMO0wfRyoLaW5loPmguOBm9B8E+mm8Y+loe54D9BORsuZbyU3ZjjkCu7ovpwnAuLaWn7UhVId2qMfgZ5R6dQcEHLFRgc2RgNCo70cIuCIy3sUHBkjxQouxmklz0Kjn3oIlB06lMXUFQY6k0hqHbbgINIfWMSWeKzUdGABimhg3x/2NZ8xyCx7Rs3SE3LWyDd0FoFkG5piQukE29OgfSWlrlAuqe1CxjdNbTQBdIFsWEA8ZIY6kA89WIbiNfg4NXfGUoO29Uf5cYzZ7/q+vz260kabrj03Dzzp3nBlarf6x6xhHM92/0lHmd9V09Wt+VwjCnk111Ih9055NN8j/Qz5OnGN1Z67b1ptXX143r9A1zTOvI="
)

_DIRECTIONS = {0: "north", 4: "east", 8: "south", 12: "west"}
_ENTITY_FIELDS = {
    "entity_number", "name", "position", "direction", "input_priority", "output_priority",
}
_ALLOWED_ENTITIES = {
    "electric-furnace", "fast-splitter", "fast-transport-belt", "inserter",
    "medium-electric-pole",
}


@dataclass(frozen=True)
class TemplateSpec:
    encoded: str
    label: str
    snap: tuple[int, int]
    counts: tuple[tuple[str, int], ...]
    sha256: str


_SPECS = {
    "start": TemplateSpec(
        REFINERY_START, "refinery_start", (12, 3),
        (("fast-splitter", 3), ("fast-transport-belt", 20)),
        "cf4312abbaa4d059fd75c6e512de05e334e8e43fa3c45de24c53462a8bb9b881",
    ),
    "middle": TemplateSpec(
        REFINERY_MIDDLE, "refinery_middle_repeating", (12, 9),
        (("electric-furnace", 6), ("fast-transport-belt", 27),
         ("inserter", 12), ("medium-electric-pole", 3)),
        "d46899117f0b4d89ad6faf0f8d00ceccc2864d63211100e25241effac482b5b3",
    ),
    "end": TemplateSpec(
        REFINERY_END, "refinery_end_merge", (12, 11),
        (("electric-furnace", 6), ("fast-splitter", 4),
         ("fast-transport-belt", 50), ("inserter", 12),
         ("medium-electric-pole", 3)),
        "d33db416e47bb8a4dbfdbd6c060466088cb12a1846d0343e37cdd43a4b0a61f2",
    ),
}


def _decode(encoded: str) -> dict:
    if not encoded.startswith("0"):
        raise ValueError("Factorio blueprint must use version prefix 0")
    try:
        raw = zlib.decompress(base64.b64decode(encoded[1:], validate=True))
        decoded = json.loads(raw)
    except (ValueError, TypeError, zlib.error, json.JSONDecodeError) as exc:
        raise ValueError("Invalid Factorio blueprint string") from exc
    if set(decoded) != {"blueprint"} or not isinstance(decoded["blueprint"], dict):
        raise ValueError("Expected one Factorio blueprint, not a book or planner")
    return decoded["blueprint"]


def _validate_entities(blueprint: dict, spec: TemplateSpec) -> None:
    entities = blueprint.get("entities")
    if not isinstance(entities, list):
        raise ValueError(f"{spec.label} has no entity list")
    numbers = [entity.get("entity_number") for entity in entities]
    if sorted(numbers) != list(range(1, len(entities) + 1)):
        raise ValueError(f"{spec.label} entity numbers are not contiguous")
    counts = Counter(entity.get("name") for entity in entities)
    if counts != Counter(dict(spec.counts)):
        raise ValueError(f"{spec.label} entity inventory changed: {dict(counts)}")
    for entity in entities:
        _validate_entity(entity, spec.label)


def _validate_entity(entity: dict, label: str) -> None:
    unknown = set(entity) - _ENTITY_FIELDS
    if unknown:
        raise ValueError(f"{label} has unsupported entity fields: {sorted(unknown)}")
    if entity.get("name") not in _ALLOWED_ENTITIES:
        raise ValueError(f"{label} has unsupported entity {entity.get('name')!r}")
    position = entity.get("position")
    if not isinstance(position, dict) or set(position) != {"x", "y"}:
        raise ValueError(f"{label} has an invalid entity position")
    if not all(isinstance(position[axis], (int, float)) for axis in ("x", "y")):
        raise ValueError(f"{label} has a non-numeric entity position")
    if entity.get("direction", 0) not in _DIRECTIONS:
        raise ValueError(f"{label} uses a non-cardinal entity direction")
    priorities = (entity.get("input_priority"), entity.get("output_priority"))
    if any(value not in {None, "left", "none", "right"} for value in priorities):
        raise ValueError(f"{label} has an invalid splitter priority")
    if any(value is not None for value in priorities) and entity["name"] != "fast-splitter":
        raise ValueError(f"{label} applies splitter priority to {entity['name']}")


def _validate_wires(blueprint: dict, spec: TemplateSpec) -> None:
    entity_numbers = {entity["entity_number"] for entity in blueprint["entities"]}
    for wire in blueprint.get("wires", []):
        if not isinstance(wire, list) or len(wire) != 4:
            raise ValueError(f"{spec.label} has an invalid wire connection")
        if wire[0] not in entity_numbers or wire[2] not in entity_numbers:
            raise ValueError(f"{spec.label} wire references a missing entity")


def decoded_template(name: str) -> dict:
    """Return one validated, repository-pinned blueprint payload."""
    try:
        spec = _SPECS[name]
    except KeyError as exc:
        raise ValueError(f"Unknown refinery template {name!r}") from exc
    blueprint = _decode(spec.encoded)
    canonical = json.dumps(blueprint, sort_keys=True, separators=(",", ":")).encode()
    if hashlib.sha256(canonical).hexdigest() != spec.sha256:
        raise ValueError(f"{spec.label} content differs from the approved blueprint")
    if blueprint.get("item") != "blueprint" or blueprint.get("label") != spec.label:
        raise ValueError(f"{spec.label} blueprint identity changed")
    if blueprint.get("snap-to-grid") != {"x": spec.snap[0], "y": spec.snap[1]}:
        raise ValueError(f"{spec.label} snap grid changed")
    _validate_entities(blueprint, spec)
    _validate_wires(blueprint, spec)
    return blueprint


def template_actions(
    name: str, *, origin_x: float = 0, origin_y: float = 0, recipe: str | None = None,
) -> list[dict]:
    """Translate one approved template into explicit BuildPlan actions."""
    actions: list[dict] = []
    for entity in decoded_template(name)["entities"]:
        action = {
            "action_type": "place_ghost",
            "entity": entity["name"],
            "position": {
                "x": origin_x + entity["position"]["x"],
                "y": origin_y + entity["position"]["y"],
            },
        }
        if "direction" in entity:
            action["direction"] = _DIRECTIONS[entity["direction"]]
        for priority in ("input_priority", "output_priority"):
            if priority in entity:
                action[priority] = entity[priority]
        if entity["name"] == "electric-furnace":
            if not recipe:
                raise ValueError(f"{name} refinery template requires a smelting recipe")
            action["recipe"] = recipe
        actions.append(action)
    return actions
