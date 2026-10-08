<!-- Path: docs/Factorio_2.1.21_Upgrade.md | Purpose: Scope the 2.1.21 upgrade, verification and rollback without rewriting prior experiments. -->

# Factorio 2.1.21 upgrade

The repository target is Factorio **2.1.21** for deterministic, disposable RL
and Laya runtimes. This is a patch upgrade within the mods' existing `2.1`
contract. The project mods now declare `base >= 2.1.0`; their versions and
serialized report schemas remain unchanged because no Lua behavior or report
shape changed.

## Repository changes

- Linux server, training worker, campaign and dashboard defaults use the private
  `~/.local/share/factorio-rl/runtime/factorio-2.1.21` runtime.
- Starting a stopped native runtime updates its managed `read-data` and
  `write-data` paths while preserving other INI entries and existing server
  settings. Dashboard server actions forward the selected runtime root.
- Training mod lists explicitly include the bundled `recycler` dependency.
  All official bundled mods must come from the same release as the executable.
- WSL uses a versioned runtime directory and a 2.1.21 archive by default.
  Native Windows uses its explicitly selected `FactorioExe` and `ReadData`;
  both must belong to the same 2.1.21 installation. Both launchers refresh
  managed config paths when starting, preserving other INI entries. WSL
  deployment also preserves existing server settings.

## Evidence and limits

Compared the official
[2.1.20 runtime API](https://lua-api.factorio.com/2.1.20/runtime-api.json) and
[2.1.21 runtime API](https://lua-api.factorio.com/2.1.21/runtime-api.json),
ignoring documentation and ordering fields. The removed
`LuaEntityPrototype.building_grid_bit_shift` member has no project reference.
No changed or removed class method/attribute was referenced by the 34 Lua files
under `factorio_mod`, `factorio_training_lab` and `training`. This is a targeted
API audit, not an exhaustive proof of runtime behavior.

All 83 focused manager/dashboard/platform/saved-world tests and 34 Lua syntax
checks pass. The Windows config migration function was executed with an
isolated PowerShell 7.5.3 runtime on Linux; this proves config preservation,
not Windows service deployment. Live smoke evidence, installed-copy hashes, runtime archive hash and
deployment backups belong in the persistent upgrade evidence directory. A
short smoke proves the exercised mechanics, not a full mission or learned
policy's success. Windows/WSL lifecycle actions require their respective hosts;
Linux tests do not establish their deployed state.

The host's installed GUI was already **2.1.21 build 87673**, package
`factorio-space-age-experimental 2.1.21-2`. The separately installed private
headless archive is
`data/factorio-2.1.21/factorio-headless_linux_2.1.21.tar.xz`, SHA-256
`ff634a91048841483f5c000c070447999cc860d61b552d3f82b612c8a87372d8`.
All bundled mod metadata reports 2.1.21. Live verification covered:

- Six deterministic exports: snapshot, recipes, research state/options,
  science and logistic inventory.
- A disposable mining curriculum episode completing provision, execution,
  observation, measured completion and recycle.
- Three Laya ops decisions, including funded iron-starter/power ghosts and a
  typed copper-coverage wait, with zero crashes. C0 passed; this short check
  does not establish C1 or C2 success.
- All 22 current native saves loading and ticking on disposable copies with
  the tested authoritative installed Lua snapshot.
- Actual installed deterministic-server and worker-01 RCON identities,
  matching executable paths and all five official bundled mods at 2.1.21.

Evidence is under the original repository's `data/factorio-2.1.21/probes/`:
`live-result.json`, `save-load-result.json`, and `native-runtime-proof.json`.
The Laya journal is under this branch worktree's
`data/laya-saved-world/compat-2121b/evidence/`. Full factory completion and
learned-policy quality remain unproven. No separate Luna implementation was
found in the repository; a distinct external system needs its own inventory.

## Deployment and rollback

Use the upgrade branch's managers from its persistent worktree; the active
development checkout may contain unrelated dirty Lua work. Before deployment,
compare source and installed copies. Preserve the authoritative installed Lua
unless a deliberate source replacement is part of this upgrade. Apply the
metadata change to the tested authoritative snapshot, then synchronize that
snapshot to each participating runtime and GUI copy.

The source branch is `codex/factorio-2.1.21-compat`, at
`data/factorio-2.1.21/code`. Saved-world preparation freezes both its server
manager and `scripts/factorio_runtime_config.sh`. Keep both files together in
any manually pinned manager snapshot. Fleet worktrees and full repository
archives already include all scripts. The host had no running worlds at the
upgrade boundary; interrupted historical cohorts stay stopped and preserved.

The main agent backed up and migrated the installed deterministic root, all
21 native training worker roots, and matching GUI project mods. Metadata was
applied to the tested authoritative installed Lua snapshot rather than
replacing it with the clean branch's older source. All installed project-mod
hashes match that snapshot. `deployment-result.json` records the roots and
hashes; `backups/` contains pre-upgrade configs, current saves and mod trees.
Both are below the original repository's `data/factorio-2.1.21/`.

At completion the deterministic server is running on loopback game/RCON ports
`34199/27017`. All 21 training workers are stopped after worker-01 verification,
and disposable probes are stopped. No autonomous runner, training controller
or Muse helper was relaunched. Existing episode/checkpoint manifests remain
historical identities; revalidate them before any continuation after a mod or
engine change. Windows and WSL source contracts were updated and tested
offline; no Windows/WSL installation was deployed from this Linux host.

Keep both runtime installations. Back up each owned config, mod set and save
before first loading it with 2.1.21. A save written by the newer engine must not
be used as an assumed 2.1.20 rollback save. The tracked source save and historical
experiment inputs remain immutable throughout verification.

Confirm runtime process, loaded bundled/project mod versions, RCON endpoint,
save identity and explicit surface/force for each smoke. Use disposable copies
to exercise snapshot/catalog/research reporting, funded deterministic ghost
execution, RL provision/observe/execute/terminate, and Laya episode/report
consumption. Do not resume a mixed-version campaign as a matched comparison.

For native replay, pass `--runtime-root` pointing to 2.1.20 and use an untouched
pre-upgrade save and the recorded mod snapshot. For WSL, pass the matching
`-FactorioVersion` and `-Archive`. Existing checkpoint compatibility approval
and comparison runtime identity checks still apply; this upgrade grants no
automatic policy or checkpoint promotion.

Lifecycle: restarting affected Factorio runtimes and their Python consumers is
required after deployment. The GUI client must use the same engine and mod
scripts before joining. Documentation/tests alone require no restart.
