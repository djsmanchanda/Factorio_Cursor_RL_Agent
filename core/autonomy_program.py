# Path: core/autonomy_program.py
# Purpose: Compile immutable autonomy phases bound to surveyed, concrete plans when supported.

from __future__ import annotations
import hashlib
import json
from typing import Mapping

from jsonschema import Draft7Validator

from core.recipe_dag import compile_recipe_dag
from planners.electronics_block import build_electronics_block
from planners.electronics_contracts import ADVANCED_CIRCUIT_RATE, PROCESSING_UNIT_RATE
from planners.electronics_world import ElectronicsWorldSpec
from planners.fluid_layouts import FLUID_RECIPES
from planners.recipe_data import LINE_RECIPES
from planners.resource_survey import allocate_electronics_world

_PHASES = ("survey", "allocate", "plan", "authorize", "build", "verify")
_CAPACITY = {"advanced-circuit": ADVANCED_CIRCUIT_RATE, "processing-unit": PROCESSING_UNIT_RATE}
_ROOT = __import__("pathlib").Path(__file__).resolve().parents[1]

def _hash(payload: object) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()

def _fixed_contract(name: str) -> tuple[int, dict, dict] | None:
    if name in FLUID_RECIPES:
        spec = FLUID_RECIPES[name]
        ingredients = dict(zip(spec["item_ingredients"], spec["item_amounts"]))
        ingredients.update(spec["fluid_ingredients"])
        products = dict(spec["item_product_amounts"]); products.update(spec["fluid_products"])
        return round(spec["craft_time"] * 60), ingredients, products
    if name in LINE_RECIPES:
        spec = LINE_RECIPES[name]
        ingredients = dict(zip(spec["ingredients"], spec["amounts"]))
        ingredients.update(spec.get("fluid_ingredients", {}))
        return round(spec["craft_time"] * 60), ingredients, {name: spec.get("product_amount", 1)}
    return None

def _catalog_matches_bundle(graph: Mapping, catalog: Mapping) -> bool:
    recipes = {recipe["name"]: recipe for recipe in catalog["recipes"]}
    for name in graph["topological_order"]:
        fixed = _fixed_contract(name)
        recipe = recipes.get(name)
        if fixed is None or recipe is None: return False
        energy, ingredients, products = fixed
        actual_ingredients = {part["name"]: part["amount"] for part in recipe["ingredients"]}
        actual_products = {part["name"]: part["amount"] for part in recipe["products"]}
        if recipe["energy_ticks"] != energy or actual_ingredients != ingredients or actual_products != products:
            return False
    return True

def _validate_program(program: Mapping) -> None:
    schema = json.loads((_ROOT / "schemas/autonomy_program.schema.json").read_text(encoding="utf-8"))
    errors = list(Draft7Validator(schema).iter_errors(dict(program)))
    if errors: raise ValueError("AutonomyProgram validation failed: " + "; ".join(error.message for error in errors))

def compile_autonomy_program(
    goal: Mapping, catalog: Mapping, *, world_spec: Mapping | None = None,
    snapshot: Mapping | None = None, block_bounds: Mapping | None = None,
) -> dict:
    graph = compile_recipe_dag(goal, catalog)
    electronics_target = goal["target"] in _CAPACITY
    if world_spec is not None and snapshot is not None:
        if block_bounds is None: raise ValueError("block_bounds are required to prove WorldSpec provenance")
        allocated = allocate_electronics_world(snapshot, block_bounds=block_bounds, validate_bundle=False)
        if dict(world_spec) != allocated: raise ValueError("Supplied WorldSpec differs from deterministic snapshot allocation")
    if electronics_target and world_spec is None and snapshot is not None:
        if block_bounds is None: raise ValueError("block_bounds are required for survey allocation")
        world_spec = allocate_electronics_world(snapshot, block_bounds=block_bounds, validate_bundle=False)
    rate_supported = electronics_target and float(goal["delta"]["rate"]) <= _CAPACITY[goal["target"]]
    concrete = rate_supported and world_spec is not None and _catalog_matches_bundle(graph, catalog)
    artifacts: dict = {"recipe_graph_hash": _hash(graph)}
    if concrete:
        surveyed_world = ElectronicsWorldSpec.from_payload(world_spec)
        artifacts.update({
            "planner": "ElectronicsBlockPlanner", "survey": dict(world_spec),
            "build_bundle": build_electronics_block(
                include_processing=goal["target"] == "processing-unit", world=surveyed_world,
            ),
        })
    requirements = {
        "survey": ["ResourceSurvey"],
        "allocate": ["ElectronicsWorldSpec" if electronics_target else "CityPlanner"],
        "plan": ["ElectronicsBlockPlanner" if electronics_target else "NamedPlanner"],
        "authorize": ["ExecutionAuthorizer"],
        "build": ["BuildPlan"] if concrete else ["concrete BuildPlan unavailable"],
        "verify": ["observed target throughput"],
    }
    artifact_hash = _hash(artifacts)
    phases = []; prior_hash = ""
    for index, kind in enumerate(_PHASES):
        identity = {"index": index, "kind": kind, "prior": prior_hash, "requirements": requirements[kind]}
        if kind in {"plan", "authorize", "build", "verify"}: identity["artifact_hash"] = artifact_hash
        phase_hash = _hash(identity)
        phases.append({"id": f"{index:02d}-{kind}-{phase_hash[:12]}", "kind": kind, "phase_hash": phase_hash, "status": "pending", "requirements": requirements[kind]})
        prior_hash = phase_hash
    seed = {"version": "1.0.0", "goal": dict(goal), "recipe_graph": graph, "artifacts": artifacts, "executable": concrete, "phases": phases}
    program = {**seed, "program_hash": _hash(seed)}
    _validate_program(program)
    return program
