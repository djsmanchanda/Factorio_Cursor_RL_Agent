# Helper Agent

Hermes is a local post-run observer for the deterministic Factorio runtime.  It
reads a bounded case packet after a run ends, writes a review report, and keeps
a separate casebook of incidents and provisional skills.

The mutable data root is normally
`~/.local/share/factorio-rl/helper_agent/`.  Helper Agent never edits repository
code, saves, mods, Git state, or the live Factorio/runner state.  Its output is
advisory only.

See `docs/deterministic/helper_agent.md` for the repository contract and
`docs/OPERATIONS.md` for lifecycle and command details.
