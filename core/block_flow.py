# Path: core/block_flow.py
# Purpose: Check exact per-material block-port conservation and shared measured source capacity without choosing strategy.

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from fractions import Fraction
from math import isfinite
from typing import Literal

MaterialKind = Literal["item", "fluid"]
PortDirection = Literal["input", "output"]
Lane = Literal["left", "right"]

NON_CLAIMS = (
    "port-rate accounting only; no geometry, routing, belt, pipe, machine, power, or runtime claim",
    "port locations and lanes are caller supplied or explicitly unknown; no geometry is inferred",
    "capacity facts are caller declarations, not live measurements or verified spare production",
    "distinct source ids are assumed to be distinct physical sources; aliasing is not detectable",
    "block-to-block linkage is counted only from caller-declared internal links; unlinked ports stay external or conditional",
    "existing allocations and proposed demand are separate facts; no strategy or priority is chosen",
)


class BlockFlowReason(str, Enum):
    """Typed fail-closed reason codes for block flow accounting."""

    INVALID_BLOCK = "invalid_block"
    DUPLICATE_BLOCK_ID = "duplicate_block_id"
    INVALID_PORT = "invalid_port"
    DUPLICATE_PORT_ID = "duplicate_port_id"
    INVALID_RATE = "invalid_rate"
    INVALID_LOCATION = "invalid_location"
    INVALID_PROVENANCE = "invalid_provenance"
    INVALID_MATERIAL = "invalid_material"
    MATERIAL_IDENTITY_COLLISION = "material_identity_collision"
    OUTPUT_PORT_HAS_SOURCE = "output_port_has_source"
    INVALID_CAPACITY = "invalid_capacity"
    DUPLICATE_SOURCE_ID = "duplicate_source_id"
    DUPLICATE_ALLOCATION_ID = "duplicate_allocation_id"
    UNKNOWN_SOURCE = "unknown_source"
    CAPACITY_MATERIAL_MISMATCH = "capacity_material_mismatch"
    INVALID_GLOBAL_BALANCE = "invalid_global_balance"
    CONSERVATION_MATERIAL_MISMATCH = "conservation_material_mismatch"
    CONSERVATION_VIOLATION = "conservation_violation"
    INVALID_LINK = "invalid_link"
    DUPLICATE_LINK_ID = "duplicate_link_id"
    DUPLICATE_LINK = "duplicate_link"
    UNKNOWN_LINK_PORT = "unknown_link_port"
    LINK_DIRECTION = "link_direction"
    LINK_MATERIAL_MISMATCH = "link_material_mismatch"
    LINKED_INPUT_HAS_SOURCE = "linked_input_has_source"
    LINK_OUTPUT_OVER_ALLOCATED = "link_output_over_allocated"
    LINK_INPUT_OVER_ALLOCATED = "link_input_over_allocated"


class BlockFlowError(ValueError):
    """The supplied block, port, capacity, or balance facts cannot be checked honestly."""

    def __init__(self, reason: BlockFlowReason, message: str) -> None:
        super().__init__(message)
        self.reason = reason
        self.code = reason.value


class FlowStatus(str, Enum):
    """Overall conservation and capacity status; shortage dominates conditional."""

    CONSERVED = "conserved"
    CONDITIONAL = "conditional"
    SHORTAGE = "shortage"


class CapacityStatus(str, Enum):
    """Per-source status, never upgraded from unknown to available without facts."""

    AVAILABLE = "available"
    SHORTAGE = "shortage"
    CONDITIONAL = "conditional"


def _exact(value: object, label: str, reason: BlockFlowReason) -> Fraction:
    if isinstance(value, bool):
        raise BlockFlowError(reason, f"{label} must be a finite number, not a boolean")
    if isinstance(value, Fraction):
        return value
    if isinstance(value, int):
        return Fraction(value)
    if isinstance(value, float):
        if not isfinite(value):
            raise BlockFlowError(reason, f"{label} must be finite")
        return Fraction(Decimal(str(value)))
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise BlockFlowError(reason, f"{label} must be finite")
        return Fraction(value)
    raise BlockFlowError(reason, f"{label} must be an int, Fraction, float, or Decimal")


def _identifier(value: object, label: str, reason: BlockFlowReason) -> str:
    if not isinstance(value, str) or not value:
        raise BlockFlowError(reason, f"{label} must be a nonempty string")
    return value


def _provenance(value: object, label: str) -> Provenance:
    if not isinstance(value, Provenance):
        raise BlockFlowError(BlockFlowReason.INVALID_PROVENANCE, f"{label} must be Provenance")
    return value


def material_text(material: Material) -> str:
    """Return a stable human-readable material identity for errors and reports."""

    return f"{material.kind}:{material.name}"


@dataclass(frozen=True, order=True)
class Material:
    """Explicit item or fluid identity; names from different kinds never merge."""

    name: str
    kind: MaterialKind

    def __post_init__(self) -> None:
        _identifier(self.name, "material name", BlockFlowReason.INVALID_MATERIAL)
        if self.kind not in ("item", "fluid"):
            raise BlockFlowError(
                BlockFlowReason.INVALID_MATERIAL,
                f"material {self.name!r} kind must be 'item' or 'fluid', not {self.kind!r}",
            )


@dataclass(frozen=True)
class Provenance:
    """Caller-declared origin of one fact; it is evidence metadata, not verification."""

    source: str
    detail: str = ""

    def __post_init__(self) -> None:
        _identifier(self.source, "provenance source", BlockFlowReason.INVALID_PROVENANCE)
        if not isinstance(self.detail, str):
            raise BlockFlowError(
                BlockFlowReason.INVALID_PROVENANCE,
                "provenance detail must be a string",
            )


@dataclass(frozen=True)
class PortLocation:
    """Exact physical port position and optional belt lane.

    ``lane`` is required so callers either declare ``"left"``/``"right"`` or pass
    ``None`` explicitly to mean unknown. It is never inferred.
    """

    x: Fraction
    y: Fraction
    lane: Lane | None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "x", _exact(self.x, "port x", BlockFlowReason.INVALID_LOCATION)
        )
        object.__setattr__(
            self, "y", _exact(self.y, "port y", BlockFlowReason.INVALID_LOCATION)
        )
        if self.lane not in (None, "left", "right"):
            raise BlockFlowError(
                BlockFlowReason.INVALID_LOCATION,
                f"port lane must be 'left', 'right', or None, not {self.lane!r}",
            )


@dataclass(frozen=True)
class Port:
    """One block port: exact material rate, direction, location, and optional source.

    ``location=None`` explicitly means the physical position is unknown.
    ``source_id=None`` on an input means the port is outside this capacity view,
    so its supply remains conditional. Output ports must pass ``source_id=None``.
    A port's provenance is the provenance of its owning block.
    """

    port_id: str
    block_id: str
    material: Material
    direction: PortDirection
    rate: Fraction
    location: PortLocation | None
    source_id: str | None

    def __post_init__(self) -> None:
        _identifier(self.port_id, "port id", BlockFlowReason.INVALID_PORT)
        _identifier(self.block_id, "port block id", BlockFlowReason.INVALID_PORT)
        if not isinstance(self.material, Material):
            raise BlockFlowError(
                BlockFlowReason.INVALID_PORT,
                f"port {self.port_id!r} material must be Material",
            )
        if self.direction not in ("input", "output"):
            raise BlockFlowError(
                BlockFlowReason.INVALID_PORT,
                f"port {self.port_id!r} direction must be 'input' or 'output'",
            )
        rate = _exact(self.rate, f"port {self.port_id!r} rate", BlockFlowReason.INVALID_RATE)
        if rate <= 0:
            raise BlockFlowError(
                BlockFlowReason.INVALID_RATE,
                f"port {self.port_id!r} rate must be positive",
            )
        object.__setattr__(self, "rate", rate)
        if self.location is not None and not isinstance(self.location, PortLocation):
            raise BlockFlowError(
                BlockFlowReason.INVALID_LOCATION,
                f"port {self.port_id!r} location must be PortLocation or None",
            )
        if self.source_id is not None:
            _identifier(self.source_id, f"port {self.port_id!r} source id", BlockFlowReason.INVALID_PORT)
            if self.direction == "output":
                raise BlockFlowError(
                    BlockFlowReason.OUTPUT_PORT_HAS_SOURCE,
                    f"output port {self.port_id!r} cannot declare a capacity source",
                )


@dataclass(frozen=True)
class ExistingAllocation:
    """One already-committed draw on a shared source, distinct from proposed demand."""

    allocation_id: str
    rate: Fraction
    provenance: Provenance

    def __post_init__(self) -> None:
        _identifier(self.allocation_id, "allocation id", BlockFlowReason.INVALID_CAPACITY)
        rate = _exact(
            self.rate,
            f"allocation {self.allocation_id!r} rate",
            BlockFlowReason.INVALID_RATE,
        )
        if rate <= 0:
            raise BlockFlowError(
                BlockFlowReason.INVALID_RATE,
                f"allocation {self.allocation_id!r} rate must be positive",
            )
        object.__setattr__(self, "rate", rate)
        _provenance(self.provenance, f"allocation {self.allocation_id!r} provenance")


@dataclass(frozen=True)
class InternalLink:
    """One explicit internal transfer from an output port to an input port.

    ``rate=None`` declares that the transfer exists but its exact rate is
    unresolved, so the affected boundary totals stay conditional. A resolved
    link allocates an exact amount of the output port; several links may fan
    out from one output only while their total stays within its rate. A linked
    input is fed internally and must not also declare an external source.
    """

    link_id: str
    output_port_id: str
    input_port_id: str
    rate: Fraction | None
    provenance: Provenance

    def __post_init__(self) -> None:
        _identifier(self.link_id, "link id", BlockFlowReason.INVALID_LINK)
        _identifier(self.output_port_id, "link output port id", BlockFlowReason.INVALID_LINK)
        _identifier(self.input_port_id, "link input port id", BlockFlowReason.INVALID_LINK)
        if self.rate is not None:
            rate = _exact(
                self.rate,
                f"link {self.link_id!r} rate",
                BlockFlowReason.INVALID_RATE,
            )
            if rate <= 0:
                raise BlockFlowError(
                    BlockFlowReason.INVALID_RATE,
                    f"link {self.link_id!r} rate must be positive",
                )
            object.__setattr__(self, "rate", rate)
        _provenance(self.provenance, f"link {self.link_id!r} provenance")


@dataclass(frozen=True)
class SharedCapacity:
    """One shared source capacity fact; one source id is counted at most once.

    ``measured_rate=None`` means the supply is unmeasured. ``existing_allocations=None``
    means existing commitments are unknown; an explicit empty tuple is the only
    representation of "no existing allocations". Neither ``None`` is treated as zero.
    """

    source_id: str
    material: Material
    measured_rate: Fraction | None
    existing_allocations: tuple[ExistingAllocation, ...] | None
    provenance: Provenance

    def __post_init__(self) -> None:
        _identifier(self.source_id, "capacity source id", BlockFlowReason.INVALID_CAPACITY)
        if not isinstance(self.material, Material):
            raise BlockFlowError(
                BlockFlowReason.INVALID_CAPACITY,
                f"capacity {self.source_id!r} material must be Material",
            )
        if self.measured_rate is not None:
            measured = _exact(
                self.measured_rate,
                f"capacity {self.source_id!r} measured rate",
                BlockFlowReason.INVALID_RATE,
            )
            if measured < 0:
                raise BlockFlowError(
                    BlockFlowReason.INVALID_RATE,
                    f"capacity {self.source_id!r} measured rate must not be negative",
                )
            object.__setattr__(self, "measured_rate", measured)
        if self.existing_allocations is not None:
            if isinstance(self.existing_allocations, (str, bytes)) or not isinstance(
                self.existing_allocations, Sequence
            ):
                raise BlockFlowError(
                    BlockFlowReason.INVALID_CAPACITY,
                    f"capacity {self.source_id!r} existing allocations must be a sequence or None",
                )
            allocations: list[ExistingAllocation] = []
            seen: set[str] = set()
            for allocation in self.existing_allocations:
                if not isinstance(allocation, ExistingAllocation):
                    raise BlockFlowError(
                        BlockFlowReason.INVALID_CAPACITY,
                        f"capacity {self.source_id!r} allocations must be ExistingAllocation",
                    )
                if allocation.allocation_id in seen:
                    raise BlockFlowError(
                        BlockFlowReason.DUPLICATE_ALLOCATION_ID,
                        f"capacity {self.source_id!r} repeats allocation "
                        f"{allocation.allocation_id!r}",
                    )
                seen.add(allocation.allocation_id)
                allocations.append(allocation)
            object.__setattr__(
                self,
                "existing_allocations",
                tuple(sorted(allocations, key=lambda item: item.allocation_id)),
            )
        _provenance(self.provenance, f"capacity {self.source_id!r} provenance")


@dataclass(frozen=True)
class GlobalBalance:
    """Explicit net external flow per material for the whole block set.

    Positive means the blocks deliver that rate outside; negative means the
    blocks draw that rate from outside. Every port material must appear exactly
    once, and the exact external boundary net (gross ports minus explicit
    internal links) must equal the declared value.
    """

    net_by_material: tuple[tuple[Material, Fraction], ...]
    provenance: Provenance

    @classmethod
    def from_mapping(
        cls,
        values: Mapping[Material, Fraction | int | float | Decimal],
        *,
        provenance: Provenance,
    ) -> GlobalBalance:
        """Build a normalized balance from an explicit material-to-net-rate mapping."""

        if not isinstance(values, Mapping) or not values:
            raise BlockFlowError(
                BlockFlowReason.INVALID_GLOBAL_BALANCE,
                "global balance must be a nonempty mapping of Material to net rates",
            )
        entries: list[tuple[Material, Fraction]] = []
        for material, raw in values.items():
            if not isinstance(material, Material):
                raise BlockFlowError(
                    BlockFlowReason.INVALID_GLOBAL_BALANCE,
                    "global balance keys must be Material",
                )
            entries.append(
                (
                    material,
                    _exact(
                        raw,
                        f"global balance rate for {material_text(material)}",
                        BlockFlowReason.INVALID_GLOBAL_BALANCE,
                    ),
                )
            )
        return cls(tuple(entries), provenance)

    def __post_init__(self) -> None:
        if isinstance(self.net_by_material, (str, bytes)) or not isinstance(
            self.net_by_material, Sequence
        ):
            raise BlockFlowError(
                BlockFlowReason.INVALID_GLOBAL_BALANCE,
                "global balance net_by_material must be a sequence of Material/rate pairs",
            )
        entries: list[tuple[Material, Fraction]] = []
        seen: set[Material] = set()
        for entry in self.net_by_material:
            if not isinstance(entry, tuple) or len(entry) != 2:
                raise BlockFlowError(
                    BlockFlowReason.INVALID_GLOBAL_BALANCE,
                    "global balance entries must be (Material, rate) pairs",
                )
            material, raw = entry
            if not isinstance(material, Material):
                raise BlockFlowError(
                    BlockFlowReason.INVALID_GLOBAL_BALANCE,
                    "global balance entries must use Material identities",
                )
            if material in seen:
                raise BlockFlowError(
                    BlockFlowReason.INVALID_GLOBAL_BALANCE,
                    f"global balance repeats {material_text(material)}",
                )
            seen.add(material)
            entries.append(
                (
                    material,
                    _exact(
                        raw,
                        f"global balance rate for {material_text(material)}",
                        BlockFlowReason.INVALID_GLOBAL_BALANCE,
                    ),
                )
            )
        if not entries:
            raise BlockFlowError(
                BlockFlowReason.INVALID_GLOBAL_BALANCE,
                "global balance must name at least one material",
            )
        object.__setattr__(
            self,
            "net_by_material",
            tuple(sorted(entries, key=lambda entry: entry[0])),
        )
        _provenance(self.provenance, "global balance provenance")


@dataclass(frozen=True)
class Block:
    """Immutable block identity with its ports and fact provenance."""

    block_id: str
    ports: tuple[Port, ...]
    provenance: Provenance

    def __post_init__(self) -> None:
        _identifier(self.block_id, "block id", BlockFlowReason.INVALID_BLOCK)
        if isinstance(self.ports, (str, bytes)) or not isinstance(self.ports, Sequence):
            raise BlockFlowError(
                BlockFlowReason.INVALID_BLOCK,
                f"block {self.block_id!r} ports must be a sequence",
            )
        ports: list[Port] = []
        seen: set[str] = set()
        for port in self.ports:
            if not isinstance(port, Port):
                raise BlockFlowError(
                    BlockFlowReason.INVALID_PORT,
                    f"block {self.block_id!r} ports must be Port facts",
                )
            if port.block_id != self.block_id:
                raise BlockFlowError(
                    BlockFlowReason.INVALID_PORT,
                    f"port {port.port_id!r} belongs to {port.block_id!r}, "
                    f"not block {self.block_id!r}",
                )
            if port.port_id in seen:
                raise BlockFlowError(
                    BlockFlowReason.DUPLICATE_PORT_ID,
                    f"block {self.block_id!r} repeats port {port.port_id!r}",
                )
            seen.add(port.port_id)
            ports.append(port)
        if not ports:
            raise BlockFlowError(
                BlockFlowReason.INVALID_BLOCK,
                f"block {self.block_id!r} must declare at least one port",
            )
        object.__setattr__(self, "ports", tuple(sorted(ports, key=lambda port: port.port_id)))
        _provenance(self.provenance, f"block {self.block_id!r} provenance")


@dataclass(frozen=True)
class MaterialFlow:
    """Exact gross, internal, and external port flow for one material.

    ``net`` is ``external_output - external_input`` after subtracting only
    caller-declared internal links. ``unresolved`` means an internal link or a
    partially linked input leaves those boundary totals unknown, so ``net`` is
    the known remainder and is not asserted against ``global_net``.
    """

    material: Material
    output: Fraction
    input: Fraction
    internal_output: Fraction
    internal_input: Fraction
    external_output: Fraction
    external_input: Fraction
    net: Fraction
    global_net: Fraction
    unresolved: bool


@dataclass(frozen=True)
class SharedCapacityReport:
    """Capacity accounting that keeps measured, allocated, proposed, and unknown distinct.

    ``spare`` is ``measured_rate - existing_total`` and may be negative.
    ``shortage`` is exact only when measured supply and existing allocations are
    both known. ``minimum_shortage`` is the guaranteed lower bound; it is nonzero
    from proposed demand alone even when existing allocations are unknown.
    """

    source_id: str
    material: Material
    measured_rate: Fraction | None
    existing_allocations: tuple[ExistingAllocation, ...] | None
    existing_total: Fraction | None
    proposed_demand: Fraction
    spare: Fraction | None
    existing_shortage: Fraction | None
    shortage: Fraction | None
    minimum_shortage: Fraction
    status: CapacityStatus
    reasons: tuple[str, ...]
    provenance: Provenance


@dataclass(frozen=True)
class BlockFlowResult:
    """Self-contained immutable evidence for one successful flow check."""

    status: FlowStatus
    blocks: tuple[Block, ...]
    global_balance: GlobalBalance
    ports: tuple[Port, ...]
    materials: tuple[MaterialFlow, ...]
    capacities: tuple[SharedCapacityReport, ...]
    internal_links: tuple[InternalLink, ...]
    unresolved_links: tuple[InternalLink, ...]
    unaccounted_inputs: tuple[Port, ...]
    unresolved_inputs: tuple[Port, ...]
    notes: tuple[str, ...] = NON_CLAIMS


def _sequence_of(value: object, expected: type, label: str, reason: BlockFlowReason) -> tuple:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise BlockFlowError(reason, f"{label} must be a non-string sequence")
    items = tuple(value)
    for item in items:
        if not isinstance(item, expected):
            raise BlockFlowError(reason, f"{label} must contain {expected.__name__} facts")
    return items


def check_block_flow(
    blocks: Sequence[Block],
    *,
    global_balance: GlobalBalance,
    capacities: Sequence[SharedCapacity] = (),
    internal_links: Sequence[InternalLink] = (),
) -> BlockFlowResult:
    """Check exact boundary conservation and shared source capacity for a block set.

    The check is deliberately narrow. Internal transfer between an output and an
    input of the same material is counted only when a caller-declared
    :class:`InternalLink` says so. Per explicit item or fluid identity, the
    remaining gross output minus gross input (the signed external boundary net)
    must equal the supplied global net balance. Input ports declaring a shared
    source are then counted once against that source's measured rate, explicit
    existing allocations, and unknown states. Unlinked or partially linked
    inputs stay conditional instead of being credited as internal supply. The
    check does not choose blocks, allocate sources, route transport, infer
    geometry, or claim runtime feasibility.
    """

    if not isinstance(global_balance, GlobalBalance):
        raise BlockFlowError(
            BlockFlowReason.INVALID_GLOBAL_BALANCE,
            "global_balance must be an explicit GlobalBalance fact",
        )
    block_list = _sequence_of(blocks, Block, "blocks", BlockFlowReason.INVALID_BLOCK)
    if not block_list:
        raise BlockFlowError(
            BlockFlowReason.INVALID_BLOCK,
            "at least one block is required",
        )
    capacity_list = _sequence_of(
        capacities, SharedCapacity, "capacities", BlockFlowReason.INVALID_CAPACITY
    )
    link_list = _sequence_of(
        internal_links, InternalLink, "internal_links", BlockFlowReason.INVALID_LINK
    )

    blocks_by_id: dict[str, Block] = {}
    for block in block_list:
        if block.block_id in blocks_by_id:
            raise BlockFlowError(
                BlockFlowReason.DUPLICATE_BLOCK_ID,
                f"block {block.block_id!r} is declared more than once",
            )
        blocks_by_id[block.block_id] = block

    ports = sorted(
        (port for block in block_list for port in block.ports),
        key=lambda port: port.port_id,
    )
    ports_by_id: dict[str, Port] = {}
    for port in ports:
        if port.port_id in ports_by_id:
            raise BlockFlowError(
                BlockFlowReason.DUPLICATE_PORT_ID,
                f"port {port.port_id!r} is declared more than once",
            )
        ports_by_id[port.port_id] = port

    sources_by_id: dict[str, SharedCapacity] = {}
    allocation_owner: dict[str, str] = {}
    for capacity in capacity_list:
        if capacity.source_id in sources_by_id:
            raise BlockFlowError(
                BlockFlowReason.DUPLICATE_SOURCE_ID,
                f"shared source {capacity.source_id!r} is declared more than once; "
                "shared capacity must not be counted twice",
            )
        sources_by_id[capacity.source_id] = capacity
        if capacity.existing_allocations is None:
            continue
        for allocation in capacity.existing_allocations:
            previous = allocation_owner.setdefault(allocation.allocation_id, capacity.source_id)
            if previous != capacity.source_id:
                raise BlockFlowError(
                    BlockFlowReason.DUPLICATE_ALLOCATION_ID,
                    f"allocation {allocation.allocation_id!r} appears on both "
                    f"{previous!r} and {capacity.source_id!r}",
                )

    kinds_by_name: dict[str, MaterialKind] = {}

    def observe(material: Material) -> None:
        previous = kinds_by_name.setdefault(material.name, material.kind)
        if previous != material.kind:
            raise BlockFlowError(
                BlockFlowReason.MATERIAL_IDENTITY_COLLISION,
                f"material name {material.name!r} is declared as both {previous} and "
                f"{material.kind}",
            )

    for port in ports:
        observe(port.material)
    for capacity in capacity_list:
        observe(capacity.material)
    for material, _ in global_balance.net_by_material:
        observe(material)

    for port in ports:
        if port.direction != "input" or port.source_id is None:
            continue
        capacity = sources_by_id.get(port.source_id)
        if capacity is None:
            raise BlockFlowError(
                BlockFlowReason.UNKNOWN_SOURCE,
                f"input port {port.port_id!r} names undeclared shared source "
                f"{port.source_id!r}",
            )
        if capacity.material != port.material:
            raise BlockFlowError(
                BlockFlowReason.CAPACITY_MATERIAL_MISMATCH,
                f"input port {port.port_id!r} is {material_text(port.material)} but source "
                f"{port.source_id!r} is {material_text(capacity.material)}",
            )

    links_by_input: dict[str, list[InternalLink]] = defaultdict(list)
    seen_link_ids: set[str] = set()
    seen_link_pairs: set[tuple[str, str]] = set()
    for link in link_list:
        if link.link_id in seen_link_ids:
            raise BlockFlowError(
                BlockFlowReason.DUPLICATE_LINK_ID,
                f"internal link {link.link_id!r} is declared more than once",
            )
        seen_link_ids.add(link.link_id)
        pair = (link.output_port_id, link.input_port_id)
        if pair in seen_link_pairs:
            raise BlockFlowError(
                BlockFlowReason.DUPLICATE_LINK,
                f"internal link from {link.output_port_id!r} to "
                f"{link.input_port_id!r} is declared more than once",
            )
        seen_link_pairs.add(pair)
        output = ports_by_id.get(link.output_port_id)
        if output is None:
            raise BlockFlowError(
                BlockFlowReason.UNKNOWN_LINK_PORT,
                f"internal link {link.link_id!r} names unknown output port "
                f"{link.output_port_id!r}",
            )
        input_port = ports_by_id.get(link.input_port_id)
        if input_port is None:
            raise BlockFlowError(
                BlockFlowReason.UNKNOWN_LINK_PORT,
                f"internal link {link.link_id!r} names unknown input port "
                f"{link.input_port_id!r}",
            )
        if output.direction != "output":
            raise BlockFlowError(
                BlockFlowReason.LINK_DIRECTION,
                f"internal link {link.link_id!r} source port "
                f"{link.output_port_id!r} is not an output",
            )
        if input_port.direction != "input":
            raise BlockFlowError(
                BlockFlowReason.LINK_DIRECTION,
                f"internal link {link.link_id!r} target port "
                f"{link.input_port_id!r} is not an input",
            )
        if output.material != input_port.material:
            raise BlockFlowError(
                BlockFlowReason.LINK_MATERIAL_MISMATCH,
                f"internal link {link.link_id!r} joins "
                f"{material_text(output.material)} to "
                f"{material_text(input_port.material)}",
            )
        if input_port.source_id is not None:
            raise BlockFlowError(
                BlockFlowReason.LINKED_INPUT_HAS_SOURCE,
                f"internally fed input port {input_port.port_id!r} also declares "
                f"external source {input_port.source_id!r}; a flow is internal or "
                "external, not both",
            )
        links_by_input[link.input_port_id].append(link)

    resolved_out: dict[str, Fraction] = defaultdict(Fraction)
    resolved_in: dict[str, Fraction] = defaultdict(Fraction)
    unresolved_links: list[InternalLink] = []
    unresolved_input_ids: set[str] = set()
    unresolved_materials: set[Material] = set()
    for link in link_list:
        output = ports_by_id[link.output_port_id]
        input_port = ports_by_id[link.input_port_id]
        if link.rate is None:
            unresolved_links.append(link)
            unresolved_input_ids.add(input_port.port_id)
            unresolved_materials.add(input_port.material)
        else:
            resolved_out[output.port_id] += link.rate
            resolved_in[input_port.port_id] += link.rate

    for port_id, allocated in resolved_out.items():
        output = ports_by_id[port_id]
        if allocated > output.rate:
            raise BlockFlowError(
                BlockFlowReason.LINK_OUTPUT_OVER_ALLOCATED,
                f"output port {port_id!r} feeds internal links totalling {allocated} "
                f"but its rate is {output.rate}; fan-out must stay within output capacity",
            )
    for port_id, allocated in resolved_in.items():
        input_port = ports_by_id[port_id]
        if allocated > input_port.rate:
            raise BlockFlowError(
                BlockFlowReason.LINK_INPUT_OVER_ALLOCATED,
                f"input port {port_id!r} receives internal links totalling {allocated} "
                f"but its rate is {input_port.rate}",
            )

    for port_id in links_by_input:
        input_port = ports_by_id[port_id]
        if port_id in unresolved_input_ids:
            continue
        if resolved_in[port_id] < input_port.rate:
            unresolved_input_ids.add(port_id)
            unresolved_materials.add(input_port.material)

    port_materials = {port.material for port in ports}
    balance_rates = dict(global_balance.net_by_material)
    balance_materials = set(balance_rates)
    missing = sorted(port_materials - balance_materials)
    extra = sorted(balance_materials - port_materials)
    if missing or extra:
        details: list[str] = []
        if missing:
            details.append(
                "ports not in the global balance: "
                + ", ".join(material_text(material) for material in missing)
            )
        if extra:
            details.append(
                "global balance materials with no ports: "
                + ", ".join(material_text(material) for material in extra)
            )
        raise BlockFlowError(
            BlockFlowReason.CONSERVATION_MATERIAL_MISMATCH,
            "global balance and block ports must name exactly the same materials ("
            + "; ".join(details)
            + ")",
        )

    outputs: dict[Material, Fraction] = defaultdict(Fraction)
    inputs: dict[Material, Fraction] = defaultdict(Fraction)
    for port in ports:
        target = outputs if port.direction == "output" else inputs
        target[port.material] += port.rate

    internal_outputs: dict[Material, Fraction] = defaultdict(Fraction)
    internal_inputs: dict[Material, Fraction] = defaultdict(Fraction)
    for port_id, allocated in resolved_out.items():
        internal_outputs[ports_by_id[port_id].material] += allocated
    for port_id, allocated in resolved_in.items():
        internal_inputs[ports_by_id[port_id].material] += allocated

    material_flows: list[MaterialFlow] = []
    for material in sorted(port_materials):
        output = outputs[material]
        input_rate = inputs[material]
        internal_out = internal_outputs[material]
        internal_in = internal_inputs[material]
        external_output = output - internal_out
        external_input = input_rate - internal_in
        net = external_output - external_input
        declared = balance_rates[material]
        unresolved = material in unresolved_materials
        if not unresolved and net != declared:
            raise BlockFlowError(
                BlockFlowReason.CONSERVATION_VIOLATION,
                f"{material_text(material)} external boundary net is {net} "
                f"({output} gross out - {internal_out} internal out - "
                f"({input_rate} gross in - {internal_in} internal in)) but the global "
                f"balance declares {declared} (difference {net - declared})",
            )
        material_flows.append(
            MaterialFlow(
                material=material,
                output=output,
                input=input_rate,
                internal_output=internal_out,
                internal_input=internal_in,
                external_output=external_output,
                external_input=external_input,
                net=net,
                global_net=declared,
                unresolved=unresolved,
            )
        )

    proposed_by_source: dict[str, Fraction] = defaultdict(Fraction)
    unaccounted: list[Port] = []
    unresolved_inputs: list[Port] = []
    for port in ports:
        if port.direction != "input":
            continue
        if port.source_id is not None:
            proposed_by_source[port.source_id] += port.rate
        elif port.port_id in links_by_input:
            if port.port_id in unresolved_input_ids:
                unresolved_inputs.append(port)
        else:
            unaccounted.append(port)

    capacity_reports: list[SharedCapacityReport] = []
    for capacity in sorted(capacity_list, key=lambda item: item.source_id):
        proposed = proposed_by_source.get(capacity.source_id, Fraction(0))
        if capacity.existing_allocations is None:
            existing_total = None
        else:
            existing_total = sum(
                (allocation.rate for allocation in capacity.existing_allocations),
                Fraction(0),
            )

        reasons: list[str] = []
        if capacity.measured_rate is None:
            spare = None
            existing_shortage = None
            shortage = None
            minimum_shortage = Fraction(0)
            status = CapacityStatus.CONDITIONAL
            reasons.append("measured supply is unknown; availability and shortage are unknown")
        elif existing_total is None:
            measured = capacity.measured_rate
            spare = None
            existing_shortage = None
            shortage = None
            minimum_shortage = max(Fraction(0), proposed - measured)
            if minimum_shortage:
                status = CapacityStatus.SHORTAGE
                reasons.append(
                    f"measured supply is {measured} and existing allocations are unknown; "
                    f"proposed demand {proposed} alone exceeds measured supply by at least "
                    f"{minimum_shortage}"
                )
            else:
                status = CapacityStatus.CONDITIONAL
                reasons.append(
                    "existing allocations are unknown; spare capacity is unknown, not "
                    "assumed zero"
                )
        else:
            measured = capacity.measured_rate
            spare = measured - existing_total
            existing_shortage = max(Fraction(0), -spare)
            shortage = max(Fraction(0), proposed - spare)
            minimum_shortage = shortage
            if shortage:
                status = CapacityStatus.SHORTAGE
                reasons.append(
                    f"measured supply is {measured}, existing allocations total "
                    f"{existing_total}, and proposed demand is {proposed}; "
                    f"explicit shortage is {shortage}"
                )
            else:
                status = CapacityStatus.AVAILABLE
                reasons.append(
                    f"measured supply is {measured}, existing allocations total "
                    f"{existing_total}, and proposed demand is {proposed}"
                )

        if existing_total is None:
            reasons.append("existing allocation total is unknown, not zero")
        if capacity.measured_rate is None:
            reasons.append("unmeasured supply remains conditional")
        capacity_reports.append(
            SharedCapacityReport(
                source_id=capacity.source_id,
                material=capacity.material,
                measured_rate=capacity.measured_rate,
                existing_allocations=capacity.existing_allocations,
                existing_total=existing_total,
                proposed_demand=proposed,
                spare=spare,
                existing_shortage=existing_shortage,
                shortage=shortage,
                minimum_shortage=minimum_shortage,
                status=status,
                reasons=tuple(reasons),
                provenance=capacity.provenance,
            )
        )

    any_shortage = any(
        report.status is CapacityStatus.SHORTAGE for report in capacity_reports
    )
    any_unknown = (
        bool(unaccounted)
        or bool(unresolved_links)
        or bool(unresolved_inputs)
        or any(
            report.status is CapacityStatus.CONDITIONAL for report in capacity_reports
        )
    )
    if any_shortage:
        status = FlowStatus.SHORTAGE
    elif any_unknown:
        status = FlowStatus.CONDITIONAL
    else:
        status = FlowStatus.CONSERVED

    return BlockFlowResult(
        status=status,
        blocks=tuple(sorted(block_list, key=lambda block: block.block_id)),
        global_balance=global_balance,
        ports=tuple(ports),
        materials=tuple(material_flows),
        capacities=tuple(capacity_reports),
        internal_links=tuple(sorted(link_list, key=lambda link: link.link_id)),
        unresolved_links=tuple(sorted(unresolved_links, key=lambda link: link.link_id)),
        unaccounted_inputs=tuple(sorted(unaccounted, key=lambda port: port.port_id)),
        unresolved_inputs=tuple(
            sorted(unresolved_inputs, key=lambda port: port.port_id)
        ),
        notes=NON_CLAIMS,
    )
