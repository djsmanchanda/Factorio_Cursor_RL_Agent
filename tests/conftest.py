# Path: tests/conftest.py
# Purpose: Share immutable fixtures and apply the repository's explicit test taxonomy.

import ast
import copy
import importlib
import inspect
import textwrap
from pathlib import Path

import pytest

from planners.electronics_block import build_electronics_block
from planners.electronics_world import load_electronics_world_spec

_FIXTURE = Path(__file__).parent / "fixtures" / "electronics_world_spec.json"

# Primary domains are mutually exclusive. Capability and cost markers below
# are orthogonal, so `-m "deterministic and not slow"` and `-m lua` remain
# meaningful without duplicating test files into artificial directories.
_TRAINING_FILES = {
    "test_training_batch_cli.py", "test_training_candidates.py",
    "test_training_compute.py", "test_training_episode.py",
    "test_training_evaluation.py", "test_training_factorio_bridge.py",
    "test_training_isolation.py", "test_training_lab_contracts.py",
    "test_training_lab_validation_lua.py", "test_training_observer.py",
    "test_training_policies.py", "test_training_population.py",
    "test_training_power.py", "test_training_research.py",
    "test_training_rewards.py", "test_training_scenarios.py",
    "test_training_scheduler.py", "test_training_store.py",
    "test_training_surrogate.py", "test_zoning.py",
    "test_zoning_allocation.py", "test_zoning_geometry.py",
    "test_zoning_scoring.py",
}
_OPS_HINTS = (
    "campaign", "controls", "dashboard", "journal", "log_retention",
    "observatory", "research_commands", "runner", "server", "worker",
)
_DETERMINISTIC_HINTS = (
    "alternating_scheduler", "autonomous", "baseline", "bootstrap",
    "construction_stock", "extraction", "ghost_diagnostics", "mall",
    "material_reservations", "mine_", "mining_coverage", "mission_state",
    "plate_", "power_and_belt", "power_district", "prep_", "priority",
    "recoverable_retirement", "refinery", "run_loop", "stage_chemical",
    "starter_", "stock_gating",
)

# This list is intentionally small and reviewable. The fast gate protects the
# highest-value safety/contracts across all domains; breadth remains in the
# scheduled/manual full suite instead of masquerading as fast validation.
_FAST_FILES = {
    "test_action_catalog.py", "test_bootstrap_district.py",
    "test_capacity_model.py", "test_construction_stock.py",
    "test_factory_invariants.py", "test_fluid_routing.py",
    "test_fluid_systems.py", "test_infrastructure.py",
    "test_material_reservations.py", "test_mission_state.py",
    "test_module_integrity.py", "test_plan_self_collision.py",
    "test_preflight.py", "test_recipe_catalog_contract.py",
    "test_resource_district_state.py", "test_run_loop_bounds.py",
    "test_runner_log_retention.py", "test_sandbox_contracts.py",
    "test_science_recipe_graph.py", "test_training_isolation.py",
    "test_test_taxonomy.py",
    "test_training_power.py", "test_training_rewards.py",
    "test_transport_occupancy.py",
}
_SLOW_FILES = {
    "test_electronics_bundle_preflight.py", "test_electronics_cli.py",
    "test_resource_district_variants.py", "test_training_candidates.py",
    "test_world_generation.py",
}
_SLOW_NODE_FRAGMENTS = {
    "test_infrastructure.py::test_composed_processing_bundle",
    "test_infrastructure.py::test_composed_bundle_validator",
    "test_training_scenarios.py::test_curriculum_generates_one_hundred",
    "test_training_scenarios.py::test_staged_obstacles_are_seeded",
}
_EXHAUSTIVE_NODE_FRAGMENTS = {
    "test_resource_district_variants.py::test_identical_inputs_produce_byte_stable_metrics",
    "test_training_candidates.py::test_one_hundred_scenarios",
    "test_training_candidates.py::test_candidates_anchor_the_first_pole",
    "test_training_candidates.py::test_candidates_supply_the_delivery_inserter",
    "test_training_candidates.py::test_staged_drill_prefixes",
    "test_training_candidates.py::test_staged_routes_turn",
    *_SLOW_NODE_FRAGMENTS,
}
_LUA_FILES = {
    "test_atomic_removal_preflight.py", "test_logistic_sections_lua.py",
    "test_recipe_catalog_lua.py", "test_research_lua.py",
    "test_science_telemetry_lua.py", "test_training_lab_validation_lua.py",
}
_LUA_NODE_FRAGMENTS = {
    "test_bootstrap_supply.py::test_generated_seed_lua_is_syntactically_valid",
}
_LOOPBACK_NODE_FRAGMENTS = {
    "test_observatory_control.py::test_training_control_endpoints",
    "test_training_observer.py::test_http_dashboard",
}
_INTEGRATION_FILES = {
    "test_electronics_cli.py", "test_linux_deterministic_campaign.py",
}
_LIVE_REGRESSION_FILES = {
    "test_bootstrap_priorities.py", "test_cohesive_smelter_expansion.py",
    "test_extraction_separation.py", "test_plate_bootstrap_circle.py",
}
_SOURCE_TRIPWIRE_FILES = {
    "test_executor_settings_coverage.py",
    "test_research_lua.py",
}
_SOURCE_TRIPWIRE_NODE_FRAGMENTS = {
    "test_landfill_executor_contract.py::test_lua_executor",
    "test_power_district.py::test_power_plans_request_runtime_atomic_preflight",
    "test_recipe_catalog_lua.py::test_recipe_catalog_uses",
    "test_refinery_blueprints.py::test_lua_executor_applies",
    "test_science_telemetry_lua.py::test_science_status_module_is_registered_from_control",
    "test_stock_gating.py::test_the_executor_applies_the_gate",
    "test_stock_gating.py::test_the_gate_is_a_reapplied_setting_not_a_creation_time_field",
    "test_stock_gating.py::test_a_gate_that_does_not_land_is_reported_rather_than_swallowed",
    "test_stock_gating.py::test_the_gate_is_verified_after_it_is_written",
    "test_stock_gating.py::test_the_executor_clears_and_verifies_an_old_gate",
    "test_upgrades_lua.py::test_upgrade_module_uses_the_native_order_api",
}

# These caches and ledgers are process-global in production because one runner
# owns one episode. Tests exercise many episodes in one interpreter, so restore
# their pre-test contents explicitly instead of depending on collection order.
_ISOLATED_MUTABLE_STATE = {
    "orchestrator.autonomous_builder": (
        "_STAGE_DELIVERY_PROVIDERS",
        "_REFINERY_SITE_RESERVATIONS",
        "_TRANSFERABLE_WAITS",
        "_BLOCKING_MALL_ITEMS",
        "_DRAIN_WATCH_LAST_TRANSFERABLE",
        "_DRAIN_WATCH_PREVIOUS_TRANSFERABLE",
        "UNBACKED_DRAWS",
        "MANAGED_INTERMEDIATE_SOURCES",
        "_LOAN_GATE_REFRESH_ATTEMPTS",
        "_BOOTSTRAP_SHARED_PROVIDER_ITEMS",
        "_MALL_REFRESH_SIGNATURES",
        "_MALL_PROVIDER_CAPACITY_FLOORS",
        "_MALL_STOCK_GATE_FLOORS",
        "_MALL_ITEM_PROVIDER_LIMITS",
        "_CRAFT_PROOF_CONSUMED",
    ),
    "orchestrator.resource_patches": ("_PATCH_CACHE",),
    "orchestrator.stage_extraction": ("_NEW_DIRECT_MINE_CACHE",),
    "orchestrator.stage_services": ("_PENDING_POWER_BRIDGES",),
    "orchestrator.stage_chemical": ("_CRUDE_EXPANSION_FAILED_CELLS",),
    "orchestrator.live_base": ("_MALFORMED_STOCK_CHUNKS",),
}


def _primary_domain(filename: str) -> str:
    if filename in _TRAINING_FILES or filename.startswith("test_training_"):
        return "training"
    if any(hint in filename for hint in _OPS_HINTS):
        return "ops"
    if any(hint in filename for hint in _DETERMINISTIC_HINTS):
        return "deterministic"
    return "contracts"


def _module_source_names(path: Path) -> frozenset[str]:
    """Find module constants populated from inspect.getsource calls."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, SyntaxError):
        return frozenset()

    names = set()
    for statement in tree.body:
        value = getattr(statement, "value", None)
        if value is None or not any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "inspect"
            and node.func.attr == "getsource"
            for node in ast.walk(value)
        ):
            continue
        targets = statement.targets if isinstance(statement, ast.Assign) else (
            [statement.target] if isinstance(statement, ast.AnnAssign) else []
        )
        names.update(target.id for target in targets if isinstance(target, ast.Name))
    return frozenset(names)


def _source_tripwire_matches(
    filename: str,
    nodeid: str,
    item_source: str,
    module_source_names: frozenset[str] = frozenset(),
) -> bool:
    """Return whether one collected case asserts directly against source text."""
    if (
        filename in _SOURCE_TRIPWIRE_FILES
        or any(fragment in nodeid for fragment in _SOURCE_TRIPWIRE_NODE_FRAGMENTS)
        or "inspect.getsource(" in item_source
    ):
        return True
    if not module_source_names:
        return False
    try:
        item_tree = ast.parse(textwrap.dedent(item_source))
    except SyntaxError:
        return False
    referenced_names = {
        node.id
        for node in ast.walk(item_tree)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
    }
    return bool(referenced_names & module_source_names)


def _collected_item_source(item: pytest.Item) -> str:
    try:
        return inspect.getsource(item.obj)
    except (OSError, TypeError):
        return ""


def _restore_mutable_value(module, name: str, snapshot) -> None:
    current = getattr(module, name)
    if isinstance(snapshot, dict) and isinstance(current, dict):
        current.clear()
        current.update(copy.deepcopy(snapshot))
    elif isinstance(snapshot, set) and isinstance(current, set):
        current.clear()
        current.update(copy.deepcopy(snapshot))
    elif isinstance(snapshot, list) and isinstance(current, list):
        current.clear()
        current.extend(copy.deepcopy(snapshot))
    else:
        setattr(module, name, copy.deepcopy(snapshot))


@pytest.fixture(autouse=True)
def isolate_mutable_runtime_state():
    """Keep deterministic runner caches from leaking between test cases."""
    snapshots = []
    for module_name, names in _ISOLATED_MUTABLE_STATE.items():
        module = importlib.import_module(module_name)
        for name in names:
            snapshots.append((module, name, copy.deepcopy(getattr(module, name))))

    yield

    for module, name, snapshot in snapshots:
        _restore_mutable_value(module, name, snapshot)


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Attach domain, cost, and environment markers from one audited map."""
    module_source_names: dict[Path, frozenset[str]] = {}
    for item in items:
        path = Path(str(item.path))
        filename = path.name
        nodeid = item.nodeid
        item.add_marker(getattr(pytest.mark, _primary_domain(filename)))

        slow = filename in _SLOW_FILES or any(
            fragment in nodeid for fragment in _SLOW_NODE_FRAGMENTS
        )
        exhaustive = any(
            fragment in nodeid for fragment in _EXHAUSTIVE_NODE_FRAGMENTS
        )
        lua = filename in _LUA_FILES or any(
            fragment in nodeid for fragment in _LUA_NODE_FRAGMENTS
        )
        loopback = any(
            fragment in nodeid for fragment in _LOOPBACK_NODE_FRAGMENTS
        )
        integration = (
            filename in _INTEGRATION_FILES or lua or loopback
        )
        if slow:
            item.add_marker(pytest.mark.slow)
        if exhaustive:
            item.add_marker(pytest.mark.exhaustive)
        if lua:
            item.add_marker(pytest.mark.lua)
        if loopback:
            item.add_marker(pytest.mark.loopback)
        if integration:
            item.add_marker(pytest.mark.integration)
        if filename in _LIVE_REGRESSION_FILES:
            item.add_marker(pytest.mark.live_regression)

        if path not in module_source_names:
            module_source_names[path] = _module_source_names(path)
        if _source_tripwire_matches(
            filename,
            nodeid,
            _collected_item_source(item),
            module_source_names[path],
        ):
            item.add_marker(pytest.mark.source_tripwire)

        if filename in _FAST_FILES and not (slow or exhaustive or integration):
            item.add_marker(pytest.mark.fast)


@pytest.fixture(scope="session")
def electronics_world():
    return load_electronics_world_spec(_FIXTURE)


@pytest.fixture(scope="session")
def processing_bundle(electronics_world):
    return build_electronics_block(include_processing=True, world=electronics_world)
