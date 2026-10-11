<!-- Path: docs/rl/Laya_Strategy_Guidelines.md | Purpose: Scoped mission requirements and compact strategy priors shared by Laya's mission and ops catalogs. -->

# Laya strategy guidelines

Laya chooses factory work; deterministic tools observe, validate and execute it.
The mission uses actual starter stock and manufactured items, bot-funded
construction and isolated disposable worlds. Hard boundaries remain in
[system invariants](../system_invariants.md).

The strategy rules below are initial preferences. Explore legal alternatives
in disposable runs, preserve their outcomes, and adopt persistent policy changes
through measured evaluation. A preferred sequence is not a Factorio legality
rule. Explicit scenario constraints and selected toolkit geometry still apply.

Stable rule IDs bind these passages to `laya_mission_guidelines.json` (geometry)
and `laya_ops_guidelines.json` (ops). Edit a passage and its quotes together.
Scopes, numeric preferences and supported facts live in those catalogs and the
[runner contract](Laya_Clean_Runner.md); changing prose alone does not change
the current guided sampler's weights.

## Mission

| Rule | Requirement |
| --- | --- |
| `mission.goal` | Start from the mod_playground starter kit and reach advanced circuits, then self-expand and meet all item needs. |
| `mission.science` | After advanced circuits, manufacture and deliver the required science packs to labs and complete mining-productivity-4 research. |
| `mission.no-gifts` | Use only the starter kit and items the factory manufactures; never borrow player inventory or gifted items. |

The current scenario prohibits lab production options until both plastic (C4)
and advanced-circuit (C6) production rate phases have been measured. Unlocked
recipes, banked lab stock and construction shortages cannot bypass admission.
This explicit user constraint is enforced across produce and mall-slot options.

Standing mall limits only increase, by at least 20% per increase (rounded up).
Measured phase producers can run ungated; finite borrowed batches retain exact
craft budgets. Dependency chest holds follow each actual operation's bill and
transaction so independent funded production can continue.

Science production alone is not mission completion. Recipe, technology and rate
facts come from the world and the versioned objective, not historical handoffs.

## Decision preferences

Apply these to the current objective, measured bottleneck, accessible stock,
outstanding bills and relevant work already advancing. Missing facts stay
unknown; a rule's presence does not prove the runner can observe its inputs.

| Rule | Preference |
| --- | --- |
| `mission.phase-goals-first` | Prefer actions that close current goal gaps or unblock them; preserve useful parallel progress. |
| `mission.serve-bottleneck` | Prefer the deepest unmet input in the measured bottleneck chain. |
| `mission.make-construction-items` | Make construction items that outstanding useful builds are short of. |
| `mission.protect-goal-stock` | Defer a side build that would make every current goal build unaffordable. |
| `mission.inputs-first` | Prefer builds with stocked inputs or working input supply; establish missing inputs first. |
| `mission.conserve-scarce` | Prefer actions that preserve scarce construction stock for upcoming useful builds. |
| `mission.wait-rule` | Wait for relevant work already advancing, or when no useful legal action is available. |
| `mission.purposeful-coverage` | Extend robot coverage toward resources and sites required by current demand. |
| `mission.item-blocks` | Reserve expansion space beside each item's inputs and grow from the nearest free edge. |

## Geometry profile

These rules apply to geometry choices where the adapter offers alternatives.
The default land model is `patch`; grid rules apply only when `land_model=grid`.
The current ops adapter does not expose these site, scale and power choices.
Exact module footprints and reservation dimensions are documented in
[land planning](Laya_Land_Grid.md).

| Rule | Preference or selected-template constraint |
| --- | --- |
| `mission.land-plan` | Protect usable ore for mining, reserve nearby smelting and transport space, and keep the mall together. |
| `mission.grid-parcels` | When land_model=grid, keep each build within one 64-tile parcel and reserve lattice corridors for belts, pipes and poles. |
| `mission.grid-growth` | When land_model=grid, prefer growth into adjacent parcels near the purpose's inputs. |
| `mission.furnace-reference` | Treat 12 iron, 12 copper and 6 stone furnaces as a reference allocation; measure whether ore supply and demand justify it. |
| `mission.bot-supply` | Use logistic bots to supply one or two machines and mall machines. |
| `mission.belt-transfer` | Once demand and sustained production exceed what one or two bot-fed machines need, switch to direct belt transfers. |
| `mission.substation-areas` | Use substations only in mine and mall areas, where one substation powers many machines and there is space for it. |
| `mission.medium-poles` | Prefer medium poles outside mine and mall areas; price their full bill from live recipes. |
| `mission.big-pole-relay` | Prefer big poles for long-distance connections; compare the full route bill with alternatives. |
| `mission.quad-long-arms` | Build vertical quad slots only when their long-handed inserters are funded; regular inserters cannot substitute. |

## Bootstrap and mall

A **protected anchor** keeps its baseline recipe; bootstrap gear and cable have
this role. A **standing rotating cell** is physically built but may lend its
assembler. A **batch** is finite output from a loan, not a new standing cell.
Reliable intermediate supply does not require every bootstrap recipe to own an
unborrowable assembler. A lender choice must account for displaced production.

| Rule | Preference |
| --- | --- |
| `mission.starter-mines` | Prefer iron and copper starters first, then stone and mall capacity. |
| `mission.starter-mall` | Bootstrap with four assemblers: protected gear and cable anchors plus two rotating cells. |
| `mission.plate-lines` | Use approved six-drill mine and smelting modules for later plate capacity; expand against measured demand. |
| `mission.permanent-intermediates` | Maintain reliable gear, stick, cable and circuit supply; dedicate cells when sustained demand justifies them. |
| `mission.steel-for-poles` | Establish steel before steel-dependent pole and assembler stock is exhausted. |
| `mission.rotating-mall` | During bootstrap, keep gear and cable anchors; retarget an idle or stocked rotating cell before adding capacity. |
| `mission.stock-gates` | Gate mall stock at useful targets; products being measured for the current phase run ungated. |
| `mission.slot-settle` | Let a feasible retargeted batch reach its target; reconsider if blocked or if its displaced output is urgently needed. |

Before advanced circuits, prefer finite batches and rotating cells for occasional
needs. After advanced circuits, prefer dedicated slots for sustained needs and
dedicated lines for orders too large for a slot. Retain useful production while
the replacement builds. Reconsider a batch when its inputs cannot arrive;
waiting for deliveries and waiting on an impossible dependency are different.

## Current ops adapter

These are priors for `--actions deterministic`, using the adapter's present
transactions and lifecycle limits. They do not define the final director's
action space or grant the toolkit strategic authority. The [runner contract](Laya_Clean_Runner.md)
identifies inherited decisions and missing observations.

| Rule | Preference |
| --- | --- |
| `mission.ops-starter` | In the current ops adapter, establish a plate's direct starter before its foundation. |
| `mission.ops-starters-before-foundations` | Prefer finishing iron, copper and stone starters before foundations consume their reserved drills. |
| `mission.ops-foundation` | Prefer a mine and six-furnace foundation after its plate starter produces; reconcile the same transaction on retries. |
| `mission.ops-extend` | Extend an open refinery in place by one six-furnace module, with its mine, while its plate is still short. |
| `mission.ops-retire-starter` | Retire a starter only after its replacement delivers plates; recover entities and contents with bots. |
| `mission.ops-mall-slots` | Prefer gear, cable and circuit producers, then belt capacity, before foundations; preserve their construction budget. |
| `mission.ops-no-idle-loans` | A borrowed cell is a finite batch, not a standing producer; request it for a current shortfall and account for displaced output. |
| `mission.ops-steel-fence` | Reserve the funded steel seed's poles before competing builds consume them. |
| `mission.ops-steel` | Start the funded steel seed when iron supply is verified and lifecycle admission permits it, including the pioneer-output cycle break. |
| `mission.ops-shortage` | Reconcile current material shortfalls and produce missing items before retrying the blocked transaction. |
| `mission.ops-pending-first` | Give pending construction priority over new work, and its missing precursors priority over the final item. |
| `mission.ops-one-batch` | Let a feasible mall batch finish; interrupt when its inputs cannot arrive or its lender blocks a more urgent dependency. |
| `mission.ops-no-blind-retry` | Retry a material-blocked transaction after its required stock or allocation changes; reuse its existing plan. |
| `mission.ops-wait` | While relevant bots or coverage are advancing, wait or serve independent work. |
| `mission.ops-retry-after-wait` | Retry a construction or coverage wait after observation shows progress or its retry horizon expires. |

History priors and `mission.ops-no-idle-loans` have no numeric weights by default;
variants test them. A returned `ok` is not by itself evidence of useful output.

| Rule | Experimental preference |
| --- | --- |
| `mission.ops-history-no-repeat` | After repeated same-cause failures, prefer a different action until relevant facts change. |
| `mission.ops-history-follow-through` | After useful output, prefer continuing related work when it still serves current demand. |
| `mission.ops-history-unstick` | After several decisions without useful progress, explore an untried feasible operation. |

## Context and numeric behavior

Keep goal/gap, accessible versus reserved stock, relevant outstanding work,
blocked inputs, displaced production and recent causal outcomes in the decision.
Keep full history, rule rationale and rule-to-fact mappings in external evidence.
The ops brief currently repeats some history and rule IDs; context compaction is
an implementation task, not a reason to remove causal facts.

The guided policy multiplies every matching `prefer` entry of shown rules and
keeps an exploration floor. Mall-slot factors are mutually exclusive: core
gear/cable/circuit slots x180 and other slots x6 (`{"not": [...]}` excludes the core
products), the same effective weights as the earlier stacked 30 x 6. The
other-slots-1 variants of `ops-loans-c2-20261008b` also cut core slots to x30; the
isolated variant `ops-other-slots-1` changes only other slots. Prose
exceptions are intended strategy; numeric features do not yet encode all of them.

Geometry selects relevant rules before its count limit. Ops uses catalog order
and a character budget after its brief; neither is a tokenizer guarantee.
Changing scopes, weights, rule visibility or representation requires its own
recorded comparison. See [variant adoption](laya_variants/README.md).
