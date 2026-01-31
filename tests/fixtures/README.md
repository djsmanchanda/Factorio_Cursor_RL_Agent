<!-- Path: tests/fixtures/README.md -->
<!-- Purpose: Documents JSON snapshot fixtures used for schema validation tests. -->

# Snapshot Fixtures

This directory contains JSON fixtures for validating the
`schemas/snapshot.schema.json` contract.

## Files

### sample_snapshot.json
- Valid snapshot
- Deterministic ordering
- Conforms exactly to the schema
- Used for smoke tests

### invalid_snapshot.json
- Intentionally invalid snapshot
- Violates one or more schema constraints
- Used to verify validator failure behavior

### intent_valid.json
- Valid intent
- Conforms exactly to intent.schema.json
- Used for intent validation smoke tests

### intent_invalid.json
- Intentionally invalid intent
- Missing required fields, invalid enum, extra property
- Used to verify intent validation failure behavior
