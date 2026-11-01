# Path: tests/test_block_flow.py
# Purpose: Verify exact block-port conservation, shared capacity accounting, and fail-closed identity facts.

from __future__ import annotations

from fractions import Fraction

import pytest

from core.block_flow import (
    Block,
    BlockFlowError,
    BlockFlowReason,
    CapacityStatus,
    ExistingAllocation,
    FlowStatus,
    GlobalBalance,
    InternalLink,
    Material,
    Port,
    PortLocation,
    Provenance,
    SharedCapacity,
    check_block_flow,
)

pytestmark = pytest.mark.contracts

# Every fact below is synthetic caller-declared test input. None is a live
# measurement, bill, or claim about a Factorio runtime.


def _prov(source: str = "synthetic-test-declaration") -> Provenance:
    return Provenance(source, "synthetic test fact, not a measurement")


def _material(name: str, kind: str = "item") -> Material:
    return Material(name, kind)


def _location(x: object = 0, y: object = 0, lane: str | None = None) -> PortLocation:
    return PortLocation(x, y, lane)


def _port(
    port_id: str,
    block_id: str,
    material: Material,
    direction: str,
    rate: object,
    *,
    source_id: str | None = None,
    location: PortLocation | None = None,
) -> Port:
    return Port(port_id, block_id, material, direction, rate, location, source_id)


def _block(block_id: str, *ports: Port) -> Block:
    return Block(block_id, tuple(ports), _prov())


def _allocation(allocation_id: str, rate: object) -> ExistingAllocation:
    return ExistingAllocation(allocation_id, rate, _prov())


def _capacity(
    source_id: str,
    material: Material,
    measured_rate: object | None,
    existing_allocations: tuple[ExistingAllocation, ...] | None,
) -> SharedCapacity:
    return SharedCapacity(source_id, material, measured_rate, existing_allocations, _prov())


def _balance(pairs: dict[Material, object]) -> GlobalBalance:
    return GlobalBalance.from_mapping(pairs, provenance=_prov())


def _link(
    link_id: str,
    output_port_id: str,
    input_port_id: str,
    rate: object | None,
) -> InternalLink:
    return InternalLink(link_id, output_port_id, input_port_id, rate, _prov())


def test_exact_conservation_preserves_locations_and_item_fluid_identity():
    iron = _material("iron-plate")
    water = _material("water", "fluid")
    measured = _capacity("iron-shared", iron, Fraction(3), ())
    water_source = _capacity("water-shared", water, Fraction(1, 3), ())
    blocks = (
        _block(
            "producer",
            _port(
                "out-iron",
                "producer",
                iron,
                "output",
                Fraction(2),
                location=_location(4, 7, "left"),
            ),
            _port(
                "out-water",
                "producer",
                water,
                "output",
                Fraction(1, 3),
                location=_location(4, 8, None),
            ),
        ),
        _block(
            "consumer",
            _port("in-iron", "consumer", iron, "input", Fraction(2), source_id="iron-shared"),
            _port(
                "in-water",
                "consumer",
                water,
                "input",
                Fraction(1, 3),
                source_id="water-shared",
                location=None,
            ),
        ),
    )
    result = check_block_flow(
        blocks,
        global_balance=_balance({iron: Fraction(0), water: Fraction(0)}),
        capacities=(measured, water_source),
    )

    assert result.status is FlowStatus.CONSERVED
    flows = {flow.material: flow for flow in result.materials}
    assert flows[iron].output == flows[iron].input == Fraction(2)
    assert flows[iron].internal_output == flows[iron].internal_input == Fraction(0)
    assert flows[iron].external_output == flows[iron].external_input == Fraction(2)
    assert flows[iron].net == flows[iron].global_net == Fraction(0)
    assert flows[iron].unresolved is False
    assert flows[water].net == Fraction(0)
    assert flows[water].external_output == flows[water].external_input == Fraction(1, 3)
    assert flows[water].material.kind == "fluid"
    assert result.internal_links == ()
    assert result.unresolved_links == ()
    assert result.unresolved_inputs == ()

    ports = {port.port_id: port for port in result.ports}
    assert ports["out-iron"].location == PortLocation(Fraction(4), Fraction(7), "left")
    assert ports["out-water"].location == PortLocation(Fraction(4), Fraction(8), None)
    assert ports["in-water"].location is None
    assert result.capacities[0].measured_rate == Fraction(3)
    assert result.capacities[0].proposed_demand == Fraction(2)
    assert result.capacities[0].shortage == Fraction(0)


def test_shared_source_proposed_demand_is_summed_once_and_shortage_is_explicit():
    iron = _material("iron-plate")
    source = _capacity(
        "iron-shared", iron, Fraction(10), (_allocation("existing-a", Fraction(7)),)
    )
    blocks = (
        _block(
            "consumer-a",
            _port("in-a", "consumer-a", iron, "input", Fraction(4), source_id="iron-shared"),
        ),
        _block(
            "consumer-b",
            _port("in-b", "consumer-b", iron, "input", Fraction(4), source_id="iron-shared"),
        ),
    )
    result = check_block_flow(
        blocks,
        global_balance=_balance({iron: Fraction(-8)}),
        capacities=(source,),
    )

    report = result.capacities[0]
    assert report.proposed_demand == Fraction(8)
    assert report.existing_total == Fraction(7)
    assert report.spare == Fraction(3)
    assert report.shortage == Fraction(5)
    assert report.existing_shortage == Fraction(0)
    assert report.status is CapacityStatus.SHORTAGE
    assert result.status is FlowStatus.SHORTAGE


def test_existing_overcommitment_is_reported_separately_from_new_demand():
    iron = _material("iron-plate")
    source = _capacity(
        "iron-shared", iron, Fraction(5), (_allocation("committed", Fraction(7)),)
    )
    block = _block(
        "consumer",
        _port("in-iron", "consumer", iron, "input", Fraction(1), source_id="iron-shared"),
    )
    result = check_block_flow(
        (block,),
        global_balance=_balance({iron: Fraction(-1)}),
        capacities=(source,),
    )

    report = result.capacities[0]
    assert report.spare == Fraction(-2)
    assert report.existing_shortage == Fraction(2)
    assert report.shortage == Fraction(3)
    assert report.status is CapacityStatus.SHORTAGE
    assert any("existing allocations" in reason for reason in report.reasons)


def test_duplicate_allocation_identifiers_across_sources_are_rejected():
    iron = _material("iron-plate")
    first = _capacity("iron-a", iron, Fraction(4), (_allocation("same-id", Fraction(1)),))
    second = _capacity("iron-b", iron, Fraction(4), (_allocation("same-id", Fraction(1)),))
    block = _block(
        "consumer",
        _port("in-iron", "consumer", iron, "input", Fraction(1), source_id="iron-a"),
    )

    with pytest.raises(BlockFlowError) as excinfo:
        check_block_flow(
            (block,),
            global_balance=_balance({iron: Fraction(-1)}),
            capacities=(first, second),
        )

    assert excinfo.value.reason is BlockFlowReason.DUPLICATE_ALLOCATION_ID


def test_item_and_fluid_name_collision_is_rejected():
    item_water = _material("water", "item")
    fluid_water = _material("water", "fluid")
    block = _block(
        "mixed",
        _port("out-item", "mixed", item_water, "output", Fraction(1)),
        _port("in-fluid", "mixed", fluid_water, "input", Fraction(1)),
    )

    with pytest.raises(BlockFlowError) as excinfo:
        check_block_flow(
            (block,),
            global_balance=_balance({item_water: Fraction(1), fluid_water: Fraction(-1)}),
        )

    assert excinfo.value.reason is BlockFlowReason.MATERIAL_IDENTITY_COLLISION


def test_unmeasured_supply_remains_conditional_with_no_invented_shortage():
    iron = _material("iron-plate")
    source = _capacity("iron-unmeasured", iron, None, ())
    block = _block(
        "consumer",
        _port("in-iron", "consumer", iron, "input", Fraction(4), source_id="iron-unmeasured"),
    )
    result = check_block_flow(
        (block,),
        global_balance=_balance({iron: Fraction(-4)}),
        capacities=(source,),
    )

    report = result.capacities[0]
    assert report.measured_rate is None
    assert report.existing_total == Fraction(0)
    assert report.spare is None
    assert report.shortage is None
    assert report.minimum_shortage == Fraction(0)
    assert report.status is CapacityStatus.CONDITIONAL
    assert result.status is FlowStatus.CONDITIONAL


def test_unknown_existing_allocations_are_never_assumed_zero():
    iron = _material("iron-plate")
    unknown = _capacity("iron-unknown-allocations", iron, Fraction(5), None)
    block = _block(
        "consumer",
        _port(
            "in-iron",
            "consumer",
            iron,
            "input",
            Fraction(4),
            source_id="iron-unknown-allocations",
        ),
    )
    result = check_block_flow(
        (block,),
        global_balance=_balance({iron: Fraction(-4)}),
        capacities=(unknown,),
    )

    report = result.capacities[0]
    assert report.existing_allocations is None
    assert report.existing_total is None
    assert report.spare is None
    assert report.shortage is None
    assert report.minimum_shortage == Fraction(0)
    assert report.status is CapacityStatus.CONDITIONAL

    larger = _capacity("iron-unknown-allocations", iron, Fraction(5), None)
    larger_block = _block(
        "consumer",
        _port(
            "in-iron",
            "consumer",
            iron,
            "input",
            Fraction(6),
            source_id="iron-unknown-allocations",
        ),
    )
    oversubscribed = check_block_flow(
        (larger_block,),
        global_balance=_balance({iron: Fraction(-6)}),
        capacities=(larger,),
    )
    raised = oversubscribed.capacities[0]
    assert raised.status is CapacityStatus.SHORTAGE
    assert raised.shortage is None
    assert raised.minimum_shortage == Fraction(1)
    assert oversubscribed.status is FlowStatus.SHORTAGE


def test_missing_existing_allocations_argument_is_not_silently_zero():
    iron = _material("iron-plate")

    with pytest.raises(TypeError):
        SharedCapacity(
            source_id="iron-shared",
            material=iron,
            measured_rate=Fraction(5),
            provenance=_prov(),
        )


def test_unaccounted_input_ports_keep_the_result_conditional():
    iron = _material("iron-plate")
    block = _block(
        "consumer",
        _port("in-iron", "consumer", iron, "input", Fraction(1)),
    )
    result = check_block_flow(
        (block,),
        global_balance=_balance({iron: Fraction(-1)}),
    )

    assert result.status is FlowStatus.CONDITIONAL
    assert [port.port_id for port in result.unaccounted_inputs] == ["in-iron"]
    assert result.capacities == ()


def test_conservation_mismatch_reports_the_exact_difference():
    iron = _material("iron-plate")
    block = _block(
        "producer",
        _port("out-iron", "producer", iron, "output", Fraction(2)),
    )

    with pytest.raises(BlockFlowError) as excinfo:
        check_block_flow((block,), global_balance=_balance({iron: Fraction(0)}))

    assert excinfo.value.reason is BlockFlowReason.CONSERVATION_VIOLATION
    assert "difference 2" in str(excinfo.value)


def test_global_balance_must_name_exactly_the_port_materials():
    iron = _material("iron-plate")
    copper = _material("copper-plate")
    block = _block(
        "producer",
        _port("out-iron", "producer", iron, "output", Fraction(1)),
    )

    with pytest.raises(BlockFlowError) as missing:
        check_block_flow((block,), global_balance=_balance({copper: Fraction(-1)}))
    assert missing.value.reason is BlockFlowReason.CONSERVATION_MATERIAL_MISMATCH
    assert "ports not in the global balance" in str(missing.value)

    with pytest.raises(BlockFlowError) as extra:
        check_block_flow(
            (block,),
            global_balance=_balance({iron: Fraction(1), copper: Fraction(0)}),
        )
    assert extra.value.reason is BlockFlowReason.CONSERVATION_MATERIAL_MISMATCH
    assert "no ports" in str(extra.value)


def test_output_ports_cannot_declare_a_capacity_source():
    iron = _material("iron-plate")

    with pytest.raises(BlockFlowError) as excinfo:
        Port("out-iron", "producer", iron, "output", 1, None, "iron-shared")

    assert excinfo.value.reason is BlockFlowReason.OUTPUT_PORT_HAS_SOURCE


def test_source_material_must_match_the_input_port_material():
    iron = _material("iron-plate")
    copper = _material("copper-plate")
    source = _capacity("copper-shared", copper, Fraction(5), ())
    block = _block(
        "consumer",
        _port("in-iron", "consumer", iron, "input", Fraction(1), source_id="copper-shared"),
    )

    with pytest.raises(BlockFlowError) as excinfo:
        check_block_flow(
            (block,),
            global_balance=_balance({iron: Fraction(-1)}),
            capacities=(source,),
        )

    assert excinfo.value.reason is BlockFlowReason.CAPACITY_MATERIAL_MISMATCH


def test_undeclared_shared_source_is_rejected():
    iron = _material("iron-plate")
    block = _block(
        "consumer",
        _port("in-iron", "consumer", iron, "input", Fraction(1), source_id="missing"),
    )

    with pytest.raises(BlockFlowError) as excinfo:
        check_block_flow((block,), global_balance=_balance({iron: Fraction(-1)}))

    assert excinfo.value.reason is BlockFlowReason.UNKNOWN_SOURCE


def test_unlinked_same_material_ports_are_not_cancelled_as_internal_links():
    iron = _material("iron-plate")
    source = _capacity("external-iron", iron, Fraction(3), ())
    blocks = (
        _block("producer", _port("out-iron", "producer", iron, "output", Fraction(2))),
        _block(
            "consumer",
            _port("in-iron", "consumer", iron, "input", Fraction(2), source_id="external-iron"),
        ),
    )

    result = check_block_flow(
        blocks,
        global_balance=_balance({iron: Fraction(0)}),
        capacities=(source,),
    )

    flow = result.materials[0]
    assert flow.internal_output == flow.internal_input == Fraction(0)
    assert flow.external_output == flow.external_input == Fraction(2)
    assert flow.net == Fraction(0)
    assert result.internal_links == ()
    assert result.unaccounted_inputs == ()
    assert result.capacities[0].proposed_demand == Fraction(2)
    assert result.status is FlowStatus.CONSERVED


def test_internally_linking_an_externally_sourced_input_is_rejected():
    iron = _material("iron-plate")
    source = _capacity("external-iron", iron, Fraction(3), ())
    blocks = (
        _block("producer", _port("out-iron", "producer", iron, "output", Fraction(2))),
        _block(
            "consumer",
            _port("in-iron", "consumer", iron, "input", Fraction(2), source_id="external-iron"),
        ),
    )

    with pytest.raises(BlockFlowError) as excinfo:
        check_block_flow(
            blocks,
            global_balance=_balance({iron: Fraction(0)}),
            capacities=(source,),
            internal_links=(_link("implicit", "out-iron", "in-iron", Fraction(2)),),
        )

    assert excinfo.value.reason is BlockFlowReason.LINKED_INPUT_HAS_SOURCE


def test_explicit_internal_link_conserves_and_leaves_the_remainder_external():
    iron = _material("iron-plate")
    blocks = (
        _block("producer", _port("out-iron", "producer", iron, "output", Fraction(3))),
        _block("consumer", _port("in-iron", "consumer", iron, "input", Fraction(2))),
    )

    result = check_block_flow(
        blocks,
        global_balance=_balance({iron: Fraction(1)}),
        internal_links=(_link("internal-iron", "out-iron", "in-iron", Fraction(2)),),
    )

    flow = result.materials[0]
    assert flow.output == Fraction(3)
    assert flow.input == Fraction(2)
    assert flow.internal_output == flow.internal_input == Fraction(2)
    assert flow.external_output == Fraction(1)
    assert flow.external_input == Fraction(0)
    assert flow.net == flow.global_net == Fraction(1)
    assert flow.unresolved is False
    assert result.unaccounted_inputs == ()
    assert result.unresolved_inputs == ()
    assert result.status is FlowStatus.CONSERVED


def test_fanout_double_counting_is_rejected_but_fanout_within_capacity_is_allowed():
    iron = _material("iron-plate")
    blocks = (
        _block("producer", _port("out-iron", "producer", iron, "output", Fraction(2))),
        _block("consumer-a", _port("in-a", "consumer-a", iron, "input", Fraction(1))),
        _block("consumer-b", _port("in-b", "consumer-b", iron, "input", Fraction(1))),
    )

    with pytest.raises(BlockFlowError) as excinfo:
        check_block_flow(
            blocks,
            global_balance=_balance({iron: Fraction(0)}),
            internal_links=(
                _link("to-a", "out-iron", "in-a", Fraction(2)),
                _link("to-b", "out-iron", "in-b", Fraction(2)),
            ),
        )

    assert excinfo.value.reason is BlockFlowReason.LINK_OUTPUT_OVER_ALLOCATED

    result = check_block_flow(
        blocks,
        global_balance=_balance({iron: Fraction(0)}),
        internal_links=(
            _link("to-a", "out-iron", "in-a", Fraction(1)),
            _link("to-b", "out-iron", "in-b", Fraction(1)),
        ),
    )

    flow = result.materials[0]
    assert flow.internal_output == Fraction(2)
    assert flow.internal_input == Fraction(2)
    assert flow.external_output == Fraction(0)
    assert flow.external_input == Fraction(0)
    assert result.status is FlowStatus.CONSERVED


def test_over_allocating_one_input_is_rejected():
    iron = _material("iron-plate")
    blocks = (
        _block(
            "producer",
            _port("out-a", "producer", iron, "output", Fraction(2)),
            _port("out-b", "producer", iron, "output", Fraction(2)),
        ),
        _block("consumer", _port("in-iron", "consumer", iron, "input", Fraction(1))),
    )

    with pytest.raises(BlockFlowError) as excinfo:
        check_block_flow(
            blocks,
            global_balance=_balance({iron: Fraction(3)}),
            internal_links=(
                _link("from-a", "out-a", "in-iron", Fraction(1)),
                _link("from-b", "out-b", "in-iron", Fraction(1)),
            ),
        )

    assert excinfo.value.reason is BlockFlowReason.LINK_INPUT_OVER_ALLOCATED


def test_mixed_internal_and_external_inputs_use_only_the_external_boundary():
    iron = _material("iron-plate")
    source = _capacity("external-iron", iron, Fraction(2), ())
    blocks = (
        _block("producer", _port("out-iron", "producer", iron, "output", Fraction(2))),
        _block("internal", _port("in-internal", "internal", iron, "input", Fraction(2))),
        _block(
            "external",
            _port("in-external", "external", iron, "input", Fraction(1), source_id="external-iron"),
        ),
    )

    result = check_block_flow(
        blocks,
        global_balance=_balance({iron: Fraction(-1)}),
        capacities=(source,),
        internal_links=(
            _link("internal-iron", "out-iron", "in-internal", Fraction(2)),
        ),
    )

    flow = result.materials[0]
    assert flow.internal_output == flow.internal_input == Fraction(2)
    assert flow.external_output == Fraction(0)
    assert flow.external_input == Fraction(1)
    assert flow.net == flow.global_net == Fraction(-1)
    assert result.capacities[0].proposed_demand == Fraction(1)
    assert result.unaccounted_inputs == ()
    assert result.status is FlowStatus.CONSERVED


def test_internal_link_material_mismatch_is_rejected():
    iron = _material("iron-plate")
    copper = _material("copper-plate")
    blocks = (
        _block("producer", _port("out-iron", "producer", iron, "output", Fraction(2))),
        _block("consumer", _port("in-copper", "consumer", copper, "input", Fraction(2))),
    )

    with pytest.raises(BlockFlowError) as excinfo:
        check_block_flow(
            blocks,
            global_balance=_balance({iron: Fraction(2), copper: Fraction(-2)}),
            internal_links=(_link("bad", "out-iron", "in-copper", Fraction(2)),),
        )

    assert excinfo.value.reason is BlockFlowReason.LINK_MATERIAL_MISMATCH


def test_duplicate_internal_links_are_rejected():
    iron = _material("iron-plate")
    blocks = (
        _block("producer", _port("out-iron", "producer", iron, "output", Fraction(2))),
        _block("consumer", _port("in-iron", "consumer", iron, "input", Fraction(2))),
    )
    balance = _balance({iron: Fraction(0)})

    with pytest.raises(BlockFlowError) as duplicate_id:
        check_block_flow(
            blocks,
            global_balance=balance,
            internal_links=(
                _link("same", "out-iron", "in-iron", Fraction(1)),
                _link("same", "out-iron", "in-iron", Fraction(1)),
            ),
        )
    assert duplicate_id.value.reason is BlockFlowReason.DUPLICATE_LINK_ID

    with pytest.raises(BlockFlowError) as duplicate_pair:
        check_block_flow(
            blocks,
            global_balance=balance,
            internal_links=(
                _link("first", "out-iron", "in-iron", Fraction(1)),
                _link("second", "out-iron", "in-iron", Fraction(1)),
            ),
        )
    assert duplicate_pair.value.reason is BlockFlowReason.DUPLICATE_LINK


def test_partially_linked_input_stays_conditional_instead_of_available():
    iron = _material("iron-plate")
    blocks = (
        _block("producer", _port("out-iron", "producer", iron, "output", Fraction(3))),
        _block("consumer", _port("in-iron", "consumer", iron, "input", Fraction(2))),
    )

    result = check_block_flow(
        blocks,
        global_balance=_balance({iron: Fraction(99)}),
        internal_links=(_link("partial", "out-iron", "in-iron", Fraction(1)),),
    )

    flow = result.materials[0]
    assert flow.internal_output == flow.internal_input == Fraction(1)
    assert flow.external_output == Fraction(2)
    assert flow.external_input == Fraction(1)
    assert flow.unresolved is True
    assert result.unresolved_links == ()
    assert [port.port_id for port in result.unresolved_inputs] == ["in-iron"]
    assert result.unaccounted_inputs == ()
    assert result.status is FlowStatus.CONDITIONAL


def test_explicitly_unresolved_link_stays_conditional_and_skips_balance_equality():
    iron = _material("iron-plate")
    blocks = (
        _block("producer", _port("out-iron", "producer", iron, "output", Fraction(2))),
        _block("consumer", _port("in-iron", "consumer", iron, "input", Fraction(2))),
    )

    result = check_block_flow(
        blocks,
        global_balance=_balance({iron: Fraction(99)}),
        internal_links=(_link("unresolved", "out-iron", "in-iron", None),),
    )

    flow = result.materials[0]
    assert flow.unresolved is True
    assert flow.internal_output == flow.internal_input == Fraction(0)
    assert flow.external_output == flow.external_input == Fraction(2)
    assert [link.link_id for link in result.unresolved_links] == ["unresolved"]
    assert [port.port_id for port in result.unresolved_inputs] == ["in-iron"]
    assert result.status is FlowStatus.CONDITIONAL
