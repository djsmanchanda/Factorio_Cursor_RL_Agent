<!-- Path: data/laya-orchestration/model-launch-20261026/autonomy12h-20261027/physical-closure-notes.md | Purpose: Describe the separate physical-evidence collector and its parent-owned final accounting boundary. -->

# Physical closure preparation — 2026-10-27 IST

`collect_physical_closure.py` is a read-only physical observation seam for the original logical120 cohort. It imports only the existing adapter's pure ownership/attempt helpers. It does not invoke the adapter, project endpoints, certify raw joins, issue an adoption verdict, admit work, or perform cleanup. This session wrote only this note, the collector, and `test_physical_closure.py`; temporary synthetic fixtures were outside the repository. The shared dirty root and all active source/configuration/worlds were preserved. No live collector invocation, services, SSH, network/model calls, RCON, further delegation, commits or pushes were performed. Lifecycle action from implementation: **none**.

Routing was inspected without edits: live `orchestrator_capabilities` returned provider instance `codex_2a91aaf2-edfa-4070-85c9-50c31f3c8ef6`, model `gpt-6.1-sol`, inherited `reasoningEffort:xhigh`; that instance's local selection metadata says `personal_2`, `setupMode:existing`. The requested Personal_2/tpsmanchanda selection was preserved. No credential values were emitted or routing settings changed.

## Inputs and later invocation

The parent invokes the collector only after preparing closed, copied immutable inputs. **It has not been run against the active cohort.** Every input requires both its explicit absolute path and the expected exact-byte lowercase SHA256. There is no live path discovery or default cohort selection.

```text
python -B /absolute/path/collect_physical_closure.py
  --snapshot /absolute/immutable/host-health.json HEALTH_SHA256
  --transfer-plan /absolute/immutable/transfer-plan.json PLAN_SHA256
  --registry /absolute/original-registry /absolute/immutable/original-copy.json ORIGINAL_SHA256
  --registry /absolute/guided-registry /absolute/immutable/guided-copy.json GUIDED_SHA256
  --registry /absolute/kev-registry /absolute/immutable/kev-copy.json KEV_SHA256
  --registry /absolute/vela-registry /absolute/immutable/vela-copy.json VELA_SHA256
  --registry /absolute/jiwo-registry /absolute/immutable/jiwo-copy.json JIWO_SHA256
  [--historical-health /absolute/immutable/historical-health.json HISTORY_SHA256]
  [--scope /absolute/immutable/physical-scope.json SCOPE_SHA256]
  --output-dir /absolute/analysis/FRESH-DIRECTORY
```

The invocation above is a flag layout; combine it into one shell command or add continuations. Replace the placeholder registry paths with the exact absolute identities in the authoritative plan. `--registry` occurs exactly five times; `--historical-health` can repeat. The health snapshot must be `laya-model-cohort-host-health-v1`, the plan `laya-model-capacity-transfer-v1`, and the copied status hashes must match `health.registry_metadata`. Copies must be outside all owned registry/world roots. Stable file identity/size/mtime/ctime reads plus expected hashes reject ordinary input replacement/writes; **hashes do not prove atomicity or authenticity**. Duplicate JSON keys and non-finite values are refused.

The existing helpers enforce the exact four arms × seeds80300–80329 union, named original7, correct per-arm transferred113 owners, full source administrative113 rows, and logical rows matching their owner copies. Administrative `cancelled_queued` rows are retained in the evidence packet with **zero additional trials**, rather than being erased or turned into actual attempts. Any nonterminal logical/retained actual attempt, reservation, ownership ambiguity, or active/unparseable health context emits a blocked packet before host queries. These are physical admission guards, not a rerun of adapter reporting or semantic certification.

## Fresh physical observations

Only the five exact owned supervisors are queried, using `systemctl --user show NAME --no-pager` with explicit properties: Id, LoadState, ActiveState, SubState, MainPID, ControlPID, ControlGroup, InvocationID, Result, ExecMainCode and ExecMainStatus. Queries bracket collection; changes or errors remain blockers. Both PID fields must be zero and ActiveState inactive/failed. A failed unit's Result/ExecMainStatus remains unchanged in evidence. An unloaded/not-found unit cannot supply invented success fields. The four fields consumed by the adapter must also match the pinned health snapshot exactly; otherwise the parent needs a new health snapshot, not a patched copy.

Current/historical health and optional explicit scope supply cgroup paths; the collector never guesses one from a unit name. Each path must be under this UID's user-manager cgroup and contain the exact owned unit as a complete path component. It traverses **all descendants**, recording each `cgroup.events` and `cgroup.procs`, then rechecks events/processes/child directories. Either nonempty processes or hierarchical `populated 1` blocks emptiness. Missing/denied/changed reads, symlinks, incomplete enumeration and unknown cgroup2 mount scope remain unknown. A known pinned scope that is initially absent can be explicitly absent only with an inactive owned unit. A scope disappearing partway through traversal is unknown, never an empty tree. Absence of a parent's processes is insufficient.

Every retained actual attempt is inspected, including historical attempts and preparation failures. Each needs its actual canonical world path and disposable `manifest.json`, which pins game/RCON port numbers. Missing, aliased or invalid manifests stay blocked/unknown. The current Factorio PID file, if present, and recorded runner PIDs become identity claims; an absent PID file still requires process scanning. Claims are bound to the job, owning registry, world and attempt, not merely a PID number.

The process scan enumerates current `/proc`, reads same-UID argv/cwd transiently, and keeps only **complete absolute registry/world path tokens** (including option values and path-component descendants). Loose process-name/substring matching is never used. It checks process start ticks across inspection. The collector's own PID is excluded from path matching because its CLI contains registry arguments. No environments, raw stderr, unrelated command lines, process comm names or unrelated cwd values are emitted. Read errors and hidden/incomplete proc mount scope block emptiness. Evidence is limited to this UID's argv/cwd scan, all claimed PID identities, owned cgroups and the collector's current namespaces; it is not a host-wide forensic process attribution tool.

A present claimed PID without a matching explicit path or a previously pinned boot/start identity is **ambiguous**, never assumed released. A different boot/start identity is retained as a reused foreign PID and is not attributed to the cohort. Matching old identity or an exact current scope path blocks closure even if the other check disagrees. Historical health can supply start ticks only for the same job, registry, world, PID and role. Its checked UTC must lie within the observed current boot; when older health lacks boot_id, same-host provenance is a **parent attestation**, not independently proven. The current process's start time must never be substituted for its historical identity.

The port query is `ss --no-header --listening --numeric --tcp --udp`, without process arguments. Only rows with a declared manifest port are retained. **Either protocol** on a scoped port blocks release, including unexpected/foreign listeners. Query/parse/permission errors or missing manifest port scope stay unknown. Listener ownership is not inferred and grants no authority to stop anything. PID, network namespace and boot identities are recorded; the parent must collect on the intended host with the intended systemd/proc/cgroup/network visibility. No process, service or port mutation exists in this program.

## Optional scope receipt

When inactive units no longer expose ControlGroup, supply pinned historical health or explicit previously established scope, never an inferred empty parent. An explicit scope object has this shape (replace every placeholder):

```json
{
  "version": "laya-physical-scope-v1",
  "boot_id": "actual-current-boot-id",
  "units": {
    "laya-model-base-2123-miniship-20261026.service": {
      "cgroups": ["/user.slice/user-1000.slice/user@1000.service/laya-model-base-2123-miniship-20261026.service"]
    }
  },
  "pid_identities": [{
    "job_id": "guided-s80300",
    "attempt": 0,
    "registry": "/absolute/original-registry",
    "world": "/absolute/actual-world",
    "role": "runner",
    "pid": 12345,
    "start_ticks": "actual-previously-pinned-start-ticks",
    "boot_id": "actual-current-boot-id"
  }]
}
```

Only the exact five units are accepted; the abbreviated example can include the other four. PID role is `runner` or `factorio`. Duplicate/conflicting historical identities remain blockers. Unknown identities must be resolved from retained evidence, not invented. A reboot or inability to establish current host scope blocks this narrow collector and remains parent work.

## Output and adapter mapping

The output directory and even a dangling symlink at that name must not exist; its parent must exist. It must be outside every registry/world/input and cannot contain an input. An exclusive mkdir and exclusive file creation protect existing output. Inputs are never overwritten. A write failure can leave a partial new directory; preserve it and choose another new path after resolving the failure.

The fresh directory contains `physical-closure.json`, `unit-evidence.json`, `reservation-evidence.json`, `notes.md` and `packet-sha256.json`. The last file pins all four payloads. Queries and the packet record collection UTC times; reservation evidence separately binds the copied registry hashes and source health time. Reservations are a **snapshot observation**, not a fresh live registry read or a claim that admissions were disabled. The packet preserves all administrative source113 rows.

Exit0 is `observed_clear` within the declared physical scope. Exit3 is a blocked observation packet. Exit2 refuses invalid inputs/output or a write failure; it can leave a partial fresh output on an I/O failure. No exit code means final120 certification, atomic closure, adoption or permission for lifecycle actions. Unknown reads never yield true unit cgroup/process emptiness. Blocked packets have `closure_mapping:null`.

Only an observed-clear packet supplies `closure_mapping`: health/plan hashes, the five copied registry references, empty owned reservations plus receipt, and the five exact unit proofs plus pinned unit receipt. These fields can be copied into the **parent's** `laya-final-accounting-closure-v1` proof after inspecting collection timing/scope and authenticating the evidence. This collector deliberately does not emit that proof version or fabricate an `attempts` list.

The parent still owns immutable attempt-start/terminal/release receipts for every actual attempt, cleanup chronology, runner/Factorio/port release certification, historical attempt resolutions, raw acceptance joins/pending explanations, predicate/provenance bindings and required supplements. Fresh physical observation alone does not prove a past attempt was released at its recorded cleanup time. The parent must obtain real pinned closure evidence after this active cohort finishes, assemble the full closure proof, and only then invoke the existing adapter. No final report or successor cohort was launched here.

## Synthetic validation

```bash
python -B data/laya-orchestration/model-launch-20261026/autonomy12h-20261027/test_physical_closure.py
```

Result: **28 tests passed**. All tests inject an entirely fake host/query/filesystem seam and construct tiny temporary copied snapshots. They cover active/nonterminal/reserved refusal, retained history, descendant activity under an empty parent, hierarchical population, denied/changed traversal, failed unit queries, exact ownership/admin transfer exclusion, PID reuse and ambiguity, exact path tokens and collector exclusion, historical identity binding, missing/aliased/ambiguous manifests, unexpected listeners/query errors, failed-unit fact preservation, immutable hashes and existing/world output protection. Only this focused CPU suite was run; the completed adapter's tests and endpoint audit were not repeated. Evidence level: synthetic unit tests only.


## Parent integration correction — 2026-10-27 05:06 IST

A bounded exact-unit read showed that the original completed transient unit has
`LoadState=not-found`, `ActiveState=inactive`, zero MainPID/ControlPID and empty
ControlGroup/InvocationID. The initial collector required `loaded`, so it would
block a normal systemd garbage-collection case. The parent now accepts this
consistent absent-unit query shape, while still requiring pinned historical
cgroup scope, empty descendant cgroups, the scoped process scan, released ports,
and stable bracketing queries. Missing scope, query errors, inconsistent absence
or a populated old cgroup still block closure. Systemd's default Result/status
fields after garbage collection are not historical exit proof; attempt and
release receipts remain separately required.

Three parent integration tests cover absent-with/without historical scope,
populated retained cgroup and inconsistent absent-unit data. **31 synthetic CPU
tests passed** after this correction (the original child result was 28). No live
collector or final adapter invocation occurred.
