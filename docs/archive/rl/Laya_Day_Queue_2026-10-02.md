<!-- Path: docs/archive/rl/Laya_Day_Queue_2026-10-02.md | Purpose: Record the user's 14:00 extension and the bounded follow-up learning/evaluation queue. -->

# Daytime learning queue, October 2

The user extended work through **14:00 IST today** and requested other work
be queued. `laya-until-1400-20261002.service` is active with that shared hard
deadline and up to two minutes of checkpoint/worker cleanup. Its task-scoped
sleep inhibitor remains active while the queue runs. No work is scheduled
after this window and no real-base promotion is authorized.

The existing `background-20261002-resume` run keeps its frozen code and
reserved fine-tune/evaluation window. The new supervisor waits for it to
finish, then continues the overall work window. It does not edit the running
snapshot. An explicit predecessor pause/stop prevents automatic handoff.

## Queued stages

1. Recover complete partial-round labels and audit the corrected case inputs.
   Consumed seed blocks stay consumed and malformed trailing rows are recorded.
2. Train a **features-set-7** teacher, then evaluate it on all three levels.
   Only a matched global admission can make it the continuation; otherwise
   collection uses scripted rollout.
3. Resume levels 2/1/3 with current-sampler case history, an initial adapter
   fine-tune, further policy-iteration rounds and a final adapter fine-tune.
   Collection reserves 150 minutes for the final training/evaluation stages.
4. Evaluate a completed, offline-ready adapter against a scripted baseline on
   independent lab seeds **64001–64032**, paired across all three levels.
   Comparison reports preserve completion losses, mean/p95 costs and errors.
5. If at least 90 minutes remain, try one training epoch with micro-batch 2,
   accumulation 8, checkpointing, SDPA and the same 2,048-token dataset.
   Effective batch remains 16. Offline and paired checks follow if training
   completes. This is a throughput hypothesis; it does not replace a validated
   checkpoint automatically.

New stages shorten or skip when the remaining deadline cannot accommodate
them. This is lab validation, not the full real-opening promotion test.

## Teacher repair

In features sets 1–6, the context term was shared by two variants of the same
kind and cancelled from their relative score. Given the same two option
feature rows, primary/secondary demand could not change the preferred tier.
Set 7 adds numeric-option × observed-context interactions so speed, material
and wait tradeoffs can respond to demand and health. It adds learned capacity;
no hand-authored demand threshold or build-order rule was added. Saved old
feature models keep their original vector widths and behavior.

Verification: twenty focused tests passed, including held-out synthetic demand
values, old-vector compatibility, interrupted-seed accounting, explicit-pause
protection, deadline refusal and a subprocess smoke check. Python compilation
and scoped whitespace checks passed. Live teacher performance is queued and
has not been claimed as verified.

## Evidence and lifecycle

The queue is frozen at **`b12bb55`** under
`data/laya-pi/day-20261002-queue/code/`. Its plan, live status, per-command logs,
paired reports and cleanup records are in the parent directory. The next
learning driver will use `day-20261002-learning`, prefix `dq01`, with explicit
lineage to the predecessor's `rc01 rc02` cases and consumed rounds/seeds.

The supervisor's active state, deadline, waiting phase and sleep inhibitor were
verified. Cleanup checks worker control-group ownership before stopping a live
process, protecting the predecessor while waiting. All work uses only workers
01–04/06–13; workers 05/14 and the real deterministic runtime remain outside
the queue. No Lua changed: new Python consumers load set 7 after the handoff.
The console remains http://127.0.0.1:9138/; its active-driver phase changes when
the queued learning driver starts. Queue-specific progress is in `status.json`.

To honor a later pause, stop **both** the daytime supervisor and the existing
predecessor if it is still active; the queue's cleanup does not own the
predecessor's running workers. Saved evidence and recovery checkpoints remain.
