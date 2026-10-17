<!-- Path: docs/rl/Laya_Fluid_Observation.md | Purpose: Define the bounded fluid-aware saved-world recipe observation and status contract. -->

# Fluid-aware recipe observations

The saved-world exporter has two distinct catalogs. `recipes` retains its
existing enabled, solid-input operation recipes and membership. Geometry,
mall retargeting and ops eligibility continue consuming that catalog. Presence
in the observation catalog grants no new operation or construction authority.

`recipe_observations` is an optional additive field in the saved-world-v1
schema, keyed by actual force recipe name. Every force recipe is observed,
including disabled recipes. Entries retain typed ingredients and products,
fixed or ranged product amounts, independent/shared probability, extra-output
fraction, exact energy in ticks, categories,
force recipe productivity when available, and category-compatible prototype
crafting speeds from `get_crafting_speed("normal")`. A missing catalog remains valid historical evidence.
`enabled: true`, `false`, and `null` mean enabled, disabled and unknown;
an absent entry means unobserved, never disabled. Empty Lua arrays may serialize
as empty objects; the new schema accepts these only when empty.

`observed_recipes()` normalizes the supported subset for the v2 factory board:
a deterministic single product whose name matches the recipe, with known
positive yield, energy and ingredient amounts. The observation catalog is
authoritative when present. When absent, legacy operation recipes retain their
enabled solid recipe arithmetic. Unsupported coproduct, probabilistic or
recipe-name/product-name mappings retain raw metadata without inventing a route.
Unknown probabilities, fractional extra output and catalyst productivity also
remain unsupported. They expose `recipe_arithmetic_supported: false` and unknown
capacity.

`goal_demand()` expands supported item recipes by their observed yield and
recipe productivity, exposing typed fluid inputs as demand leaves. For example,
4 plastic/s from a recipe yielding 2 plastic per coal and 20 petroleum gas
requires 2 coal/s and 40 petroleum gas/s. Coal remains a mineable solid;
petroleum gas has `material_type: fluid` and `need_id: fluid:petroleum-gas`.
No assembler, mine or item-requester choice is created for that fluid.
Disabled recipe metadata can still describe objective prerequisites; it does
not make the recipe actionable.

The full and compact boards retain recipe observation state, availability,
category and arithmetic support. Missing metadata diagnoses `recipe_unknown`,
explicit disabling diagnoses `recipe_disabled`, and unsupported arithmetic
reports `recipe_unsupported`. Measured production can still satisfy demand
when recipe metadata is missing; availability stays unknown. This replaces the
old `recipe_unavailable` diagnosis for missing observations. Historical tests
or consumers equating missing metadata with unavailability need that expectation
updated. Direct goal acceptance and stock-demand separation keep their prior
semantics.

Item engine counters cover every observed recipe's item products, including
plastic. Existing window arithmetic therefore measures chemical item output.
Fluid engine counters are not mixed into item counters or provider inventory.
Fluid leaves report unknown measured supply, connected stock, routing and
capacity (`fluid_supply_unknown`), even if a global item counter shares their
name. General oil route selection, fluid topology, extraction and delivery
observation, and coproduct allocation remain outside this contract.

Machine attribution uses the actual configured recipe name. Supported chemical
items use exported category-compatible prototype speeds and yields, rather
than assembler constants. `capacity_per_s` and `machines_needed` describe
nominal theoretical capacity (`capacity_basis: prototype_speed`); effective
capacity remains null because modules, beacons, fluid connectivity and delivered
throughput are not established by those facts. Legacy recipe boards retain
legacy speed arithmetic. These estimates do not prove a build operation exists.

Validation uses pure contract tests plus a mocked Lua exporter -> JSON -> actual
saved-world schema -> normalized recipe -> demand/board round trip. This is
offline evidence, not a deployed or observed Factorio result.

For a future authorized run, freeze the updated Python, Lua helper and schema
together and restart the runner using that copy. The Lua helper is read by the
adapter and sent through its guarded command path; it is not an installed mod,
so this change requires no installed-mod deployment or Factorio restart by
itself. No current campaign, server, mod, save or live command was changed.
