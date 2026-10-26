<!-- Path: docs/two_machine_workflow.md | Purpose: Coordinate isolated Git tasks across two machines. -->

# Two-machine task workflow

Keep an independent Git clone on each laptop. Use one branch and worktree per
task, with an agreed file ownership list, scope, base commit SHA, and validation
command. Give each machine a distinct label (for example `laptop-a` and
`laptop-b`); these are chosen labels, not detected remote machine identities.
The helper's hostname reports only the machine running it.

1. Explicitly fetch the agreed remote on each laptop (`git fetch origin`). Pick
   and share a full immutable commit SHA that both clones contain. A moving
   branch name alone is insufficient for coordinating the same starting point.
2. From the source checkout, inspect local state and create a task workspace:

   ```bash
   python tools/task_workspace.py status
   python tools/task_workspace.py create --base <shared-full-SHA> \
     --machine laptop-a --task workspace-helper --path "../task workspaces/workspace-helper"
   ```

   Replace `<shared-full-SHA>` with the agreed SHA. Machine and task labels use
   lowercase letters/digits separated by single hyphens. Paths are relative to
   the current directory; quote spaces. The helper resolves the base to a commit
   and creates `work/laptop-a/workspace-helper` there. Existing paths (including
   empty directories) and branches are refused. Parent edits and staged changes
   stay in the parent; uncommitted files are never copied.
3. Work and test in that task's directory. Divide parallel tasks by file
   ownership; agree explicitly before overlapping files. Keep checkpoints and
   validation evidence with the task. Commit reviewed changes explicitly, then
   explicitly publish with `git push -u origin work/laptop-a/workspace-helper`.
4. The main agent/integrator explicitly fetches published branches, reviews and
   integrates them, resolves conflicts, and runs the required checks. Other
   workers fetch again and agree a new shared SHA for subsequent tasks. To
   continue an existing task on the other laptop, fetch it and explicitly check
   out its published branch there; coordinate a single writer. `create` always
   creates a new branch, so it is not a resume command. Use `git pull --ff-only`
   explicitly when updating a clean existing tracking branch.

`status` prints JSON: `hostname`, `branch` (null when detached), `head`,
`dirty_paths` (including both paths of staged renames), `upstream`, `ahead`, and
`behind`. Missing upstream/counts are null. Counts describe locally available
refs and can be stale until an explicit fetch. Run from a repository with a
commit. Status does not refresh the index. Git hooks and automatic lazy fetching
are disabled by this helper. It never automatically commits, fetches, pushes,
merges, deletes, resets, cleans, or controls services. Worktree creation changes
Git's shared branch/worktree metadata; it does not switch the parent checkout.

Do not sync `.git`, worktree metadata, runtime data, installed mods, or saves
through a file-sync service. Transfer source history through Git; provision
machine-local environments separately. Git worktrees share objects only within
one clone, not across laptops.

Campaigns use frozen builds and explicit deadlines. Commit before freezing or
record the uncommitted diff in `freeze.json`; do not edit, redeploy, or restart
under an active campaign. Queue runtime changes behind it and follow
[Factorio operations](factorio_operations.md) before any authorized lifecycle
action. A published branch or completed experiment rung does not mean adoption;
apply the [Laya variant adoption rule](rl/laya_variants/README.md). Record evidence
at the level actually reached. This workspace helper requires no runtime restart
or deployment.
