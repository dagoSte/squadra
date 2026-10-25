# Workflow update: delivery readers and project improvements

Owner authorization, 2026-10-25: implement and publish the proposed Squadra
improvements and the migliora skill, with the refinements discussed in the session.

This decision changes the prior requirement that every proof and read must continue
to bind the builder's live checkout after delivery. For new CLI tasks, delivery now
materializes the exact proved source in the registry's `candidates/` directory. The
source fingerprint keeps its original repository provenance. Review and close check
the frozen source, so the builder may continue working. Ignored dependencies are not
copied or certified by the source snapshot. Legacy tasks retain their existing rules.

A reassignment preserves task, source, scope, requirements, proofs and consumed
budgets. It retires the previous handoffs and requires the new reader's ACK. It does
not cancel a live reader or release resources. No recorded verdict may be silently
replaced through reassignment. Identical CLI verdict retries return their receipt;
an amendment explicitly names the current receipt and consumes the existing budget.

Task-bound reservations permit supersession when the owner's remaining reservations
belong to other tasks. Legacy reservations remain conservative. Status observes live
queue tickets without cleaning them or inferring the holder's process state.

Inbox follow saves its resume cursor outside the journal, after flushed output. A
cursor is not an ACK. It is bound to the registry's root, hash history and recipients;
manual `--since` and `--no-cursor` remain available. Unprinted backlog is not skipped.

Migliora inspects evidence at task close. The agent applies it within the authorized
project scope. The instruction helper manages only `AGENTS.md`, runs the declared
check and makes a local pathspec commit after required project review. It preserves
foreign staged work and native hooks. It never publishes or approves memory proposals.

New frozen-task and reassignment events use hash format 3; subsequent events retain
format 3. Historical envelopes remain unchanged. Upgrade every writer and reader
before creating these tasks. Old clients refuse the new envelope, including a cached
anchor, instead of silently interpreting it with the previous workflow.

Validation must exercise transfers, stale readers, frozen-source drift, proof reuse,
cursor restart, live queue observations and commits with foreign staged changes.
Legacy workflow, plugin bridges and complete suite reconciliation remain required.
