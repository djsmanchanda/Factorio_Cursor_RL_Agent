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
