<!-- Path: docs/deterministic/work_diagnosis.md | Purpose: Define the offline, advisory deterministic troubleshooting boundary. -->

# Offline work diagnosis

`orchestrator.work_diagnosis.diagnose(bundle)` explains recorded dependencies
without calling the runner, changing its scheduling, or executing a remedy.
`tools.diagnose_work` reads explicitly supplied files and prints JSON or Markdown.
This is the first troubleshooting slice, with no automatic runner hook. It
does not alter Laya or the incumbent deterministic controller.

The report traces a selected item/project through bootstrap work, material
bills, district lifecycles and displaced loan cells. It cites JSON pointers,
records rejected evidence, suggests mechanical recovery candidates with
preconditions, and names the next observation that can test the explanation.
Candidates require controller review and existing validators; the report grants
no execution authority. Missing layout support must remain unsupported.

## Use

Copy evidence from one episode into an offline directory for a stable replay.
Create a [version 1 bundle](../../schemas/work_diagnosis_bundle.schema.json), then:

```bash
python -m tools.diagnose_work --bundle /tmp/evidence/bundle.json --format json
python -m tools.diagnose_work --bundle /tmp/evidence/bundle.json \
  --output /tmp/new-work-diagnosis.md
```

Output defaults to stdout. `--output` creates a new file and refuses to overwrite
any existing file, including an evidence file. Inputs are never saved back.
The command does not discover servers, select the latest episode, start models,
call RCON, deploy mods, or manage processes. File inputs are capped at 4 MiB,
bundles at 24 artifacts, combined graphs at 96 edges, and reports at 12,000
characters. JSON remains parseable; omitted records are counted. Narrow the
bundle when graph limits reject an artifact or output omissions hide detail.

A minimal envelope for an existing material ledger:

```json
{
  "version": 1,
  "identity": {
    "episode_id": "episode-example",
    "surface": "nauvis",
    "force": "player",
    "source_id": "dedicated-server-A"
  },
  "objective": "Build drills",
  "focus": "item:electric-mining-drill",
  "as_of_tick": null,
  "artifacts": [{
    "ref": "materials.json",
    "kind": "materials",
    "source_id": "dedicated-server-A",
    "observation_tick": null,
    "path": "materials.json"
  }]
}
```

Paths are relative to the envelope file. `ref` is a short stable raw-evidence
label used by JSON pointers. Pure callers supply `payload` instead of `path`.
Optional `provenance` records declared `run_id`, `code_revision`, `mod_revision`
and `save_sha256`; absence remains unknown. The collector must establish these
facts from the episode manifest, rather than assume current HEAD ran. File
inputs also carry their SHA-256 in the report.

## Evidence contracts and limits

`work`, `materials`, and `district` accept version 1 native journal payloads.
Use one snapshot per journal (one per district for `district`). Bootstrap work
only contains an episode ID; its source/surface/force are collector attribution
and that limitation is reported. Material and district journals contain
surface/force, but their server source also requires collector attribution.
No timestamps or current readiness are invented for persisted journals.

`loans` accepts a version 1 observation with `episode_id`, `surface`, `force`,
`source_id`, and `loans`: records with `loan_id`, `original_recipe`,
`current_recipe`, `target_item`. The collector derives a stable `loan_id` from
the exact requester position and side. A displaced cell does not establish
that every producer of the original item is unavailable. Alternative delivery,
borrower progress, restoration debt and ownership must be measured separately.
The tool does not perform a live loan export itself.

`measurements` accepts the same identity fields, `version: 1`, an integer `tick`,
and `measurements`: records with `work_id`, `counter_id`, `metric`, `value`.
Work IDs use `item:`, `project:`, `district:`, or `loan:` prefixes. Supported
metrics are cumulative `crafts_completed`, `items_delivered`, `entities_built`,
or the exact owned `pending_ghosts`. `counter_id` identifies one consistent
entity/transaction generation and measurement scope; reset it when that scope
changes or ghosts are canceled. Item-wide counters do not prove delivery to
an individual project. Reports distinguish item work from project work.

Two comparable, increasing game ticks within `max_age_ticks` (default 600) of
`as_of_tick` are needed to report recent progress. This window governs evidence
freshness, not a stall deadline. Wrapper and payload ticks must match; future
observations are rejected. Same-tick samples, stale samples and counter resets
cannot establish progress. Unrelated counters cannot count as focus progress;
prerequisite progress is reported separately. Reservations, total stock, queue
revisions and historical lifetime output do not prove current deliverability.

Recorded cycles are hypotheses to verify against current dependencies and
alternative producers. They do not establish deadlock: seeded stock or another
producer can permit progress. A fact-identical pair reports `unchanged`, with
no automatic stall verdict. Healthy work still retains hard total run budgets.
The graph is partial; services, routes, power, rejected plans and attempts are
unknown unless later observation adapters supply them. This slice makes no
throughput-improvement claim and does not replace validators or policy.

Required lifecycle action: none. The standalone command is available after
checkout; adopting an automatic runner hook is separate future work.
