<!-- Path: docs/deterministic/run_context.md | Purpose: Give observers and coding agents bounded, verifiable run evidence. -->

# Run context instead of full-log handoffs

Keep raw runner logs, decision events, submitted plans/executor reports and
inventory history as evidence. Give the coding agent a bounded packet first;
expand only the evidence needed for its current hypothesis.

```bash
.venv/bin/python -m tools.run_context \
  --log ~/.local/share/factorio-rl/deterministic/logs/autonomous-run.log \
  --inventory-history ~/.local/share/factorio-rl/deterministic/logs/inventory-history.json \
  --output /tmp/factorio-context.md
```

The packet scopes the newest run, caps text at 12,000 characters, preserves
terminal evidence and points back to raw file/line references. It records
milestones, recent decision evidence and repeated categories, rather than
copying every polling iteration. Missing data, incomplete runs and omitted
text are explicit. This is an evidence index, not an automated root-cause
verdict; logs may omit an earlier causal event.

Inventory history is joined by the run start identity, never by whichever
snapshot happens to be newest. Sample ticks and net item changes describe
stock movement; they cannot establish production throughput or available
supply. A completed run's post-run samples may reflect an idling factory, so
compare sample times with the terminal interval before attributing them to the
runner. Keep transferable, requester/buffer and per-network measurements
separate; absent telemetry stays unknown.

The campaign writes `logs/latest-context.md` at checkpoints. Its journal keeps
short status/inventory observations and a packet reference. The observer reads
the current packet and appends only changed facts, uncertainty and the next
measurement. The raw log remains available by line reference. The helper uses
the same bounded context so timeout-prone sessions do not grow through full-log
repetition. A terminal packet can be generated without any model or live server.

## What to send for a fix

Send the packet and a precise question, such as “Why is this fast-belt ghost at
(46.5, -11.5)?” Add the owning submitted plan and executor report if known.
Include the episode manifest when deployment/provenance matters. The packet's
header is historical evidence; current Git HEAD does not prove what ran.

The coding agent should identify the owning decision and inspect its actual
inputs. For a stalled cell, obtain one aligned observation of recipe amounts,
requester/machine input and output, statuses, reservations and transferable
stock, followed by a second observation if a rate is needed. Read-only
inspection of the preserved failed episode is preferable to another long
bootstrap when that inspection is already authorized.

Keep three outcome labels separate: factory improvement, useful diagnostic
information, and inconclusive/regressed. Before the next full run, state which
milestone the fix should change and what would falsify the diagnosis. The cheap
observer can collect broadly; the coding agent verifies decisive claims against
raw evidence before changing behavior.

## Cross-run search and one-call investigation

Use a local SQLite full-text index as a rebuildable cache of run evidence. The
campaign updates `logs/run-history.sqlite` after RUN END from the current log
and archived runner logs. No database server or vector embedding service is
needed. It indexes raw run evidence, including early events omitted from the compact
packet, not model-generated root-cause labels. Search results remain bounded.

```bash
.venv/bin/python -m tools.run_history index --db /tmp/run-history.sqlite --log /path/to/run.log
.venv/bin/python -m tools.run_history search --db /tmp/run-history.sqlite --query 'electric-mining-drill'
.venv/bin/python -m tools.run_evidence --log /path/to/run.log \
  --query 'BELT ECONOMY' --query 'mining_iron-ore' \
  --position 46.5 -11.5 --artifact /path/to/submitted-plan.json
```

Historical lookup returns a bounded result with run identities and original
line references. Coordinate lookup searches JSON objects structurally, so
spacing/key order do not hide an action; results include JSON pointers and
entity/direction fields. Supply episode-owned plan/report paths, since a
matching coordinate in another episode does not prove ownership in this one.
Neither retrieval command changes factory state.

Default handoff: current packet plus the specific question. Add one historical
search result when comparing repeated failures, and one batched evidence lookup
when entity ownership or plan provenance is disputed. This avoids both a giant
prompt and a long sequence of one-line searches. The SQLite cache can be rebuilt
from raw logs; raw reports and manifests remain authoritative.

## Operations Console

The home page's **Run evidence & history** link opens a read-only panel with
10-second packet refresh, last log modification time, copy/download handoff
controls, and up to five historical search matches. It works from local files
while Factorio is stopped. Missing history and refresh failures are shown
explicitly; failed refreshes retain the last packet with a stale warning.
Restart the Operations Console after Python endpoint changes, then refresh the
browser. This does not require restarting Factorio or the campaign runner.
