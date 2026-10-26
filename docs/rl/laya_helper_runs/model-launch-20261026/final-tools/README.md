<!-- Path: docs/rl/laya_helper_runs/model-launch-20261026/final-tools/README.md | Purpose: Preserve reviewed final-cohort tools together with their sibling dependencies. -->

# Logical120 final-accounting tools

These six files are byte-identical copies of the reviewed operational tools in
`data/laya-orchestration/model-launch-20261026/autonomy12h-20261027/`.
`source-manifest.json` pins their hashes. Keep the Python files together: the
physical collector imports the adapter beside it, and each test imports its
sibling implementation. Original Path comments and notes preserve provenance.

The physical collector observes only declared units, cgroups, processes, ports
and reservation snapshots. It performs no cleanup. The adapter separately needs
parent-certified attempt/release, raw-journal, identity, latency, capacity,
resource and fault evidence. Read both contract notes before invocation.

Do not run either live tool until all 120 logical jobs are terminal, all five
supervisors are inactive and scoped resources have been released. Synthetic
unit tests do not establish those conditions or authorize adoption.

Focused verification (from this directory):

```bash
python3 -B test_final_accounting.py
python3 -B test_physical_closure.py
```

No runner, policy, serving package, active freeze or world is changed by this
packaging. Lifecycle: nothing to restart or deploy.
