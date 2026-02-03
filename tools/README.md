<!-- Path: tools/README.md -->
<!-- Purpose: Describe how to run local tooling for snapshot validation. -->

# Tools

## Snapshot Validator

### Install dependency
- `pip install -r requirements.txt`

### Run
- `python tools/validate_snapshot.py <path-to-snapshot.json>`

The validator loads [schemas/snapshot.schema.json](schemas/snapshot.schema.json) and fails loudly on any schema mismatch.

## Snapshot Inspector

### Run
- `python tools/inspect_snapshot.py <path-to-snapshot.json>`

This tool validates the snapshot and prints deterministic facts about entities and bounds.

## Metrics Inspector

### Run
- `python tools/inspect_metrics.py <path-to-snapshot.json>`

This tool validates the snapshot and prints derived metrics without making decisions.

## Intent Inspector

### Run
- `python tools/inspect_intents.py <path-to-intent.json>`

This tool validates an intent against [schemas/intent.schema.json](schemas/intent.schema.json)
and prints it in a human-readable format.

## Plan Skeleton Inspector

### Run
- `python tools/inspect_plan_skeleton.py <path-to-plan-skeleton.json>`

This tool validates a plan skeleton against
[schemas/plan_skeleton.schema.json](schemas/plan_skeleton.schema.json)
and prints it in a human-readable format.

## Phase Result Inspector

### Run
- `python tools/inspect_phase_result.py <path-to-plan-skeleton.json>`

This tool evaluates a read-only phase result from a plan skeleton and prints it.

## Planning Bundle Inspector

### Run
- `python tools/inspect_planning_bundle.py <path-to-plan-skeleton.json> <context.json> [metrics.json]`

This tool runs the phase orchestrator and prints a validated planning bundle.

## Fixture Smoke Tests

### Valid fixture
- `python tools/validate_snapshot.py tests/fixtures/sample_snapshot.json`

### Invalid fixture (expected to fail)
- `python tools/validate_snapshot.py tests/fixtures/invalid_snapshot.json`
