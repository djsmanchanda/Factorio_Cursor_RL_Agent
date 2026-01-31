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

## Fixture Smoke Tests

### Valid fixture
- `python tools/validate_snapshot.py tests/fixtures/sample_snapshot.json`

### Invalid fixture (expected to fail)
- `python tools/validate_snapshot.py tests/fixtures/invalid_snapshot.json`
