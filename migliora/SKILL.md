---
name: migliora
description: Improve a project's development workflow from observed failures, repeated work and avoidable waits. Apply scoped, verified remedies and useful project instructions, then automatically commit owned changes when the project's required checks and reviews are complete. Use after recurring development friction or when asked to improve the process; do not add rules without evidence.
---

# Migliora

Answer in the user's language. This skill is bundled with Squadra and also works
when the user names it directly. Resolve this directory's real location; the
bundled CLI is `../scripts/squadra.py` and the detailed workflow is
[the migliora guide](../references/skills/migliora.md). Read that guide.

Read the project's operating instructions and recall its lessons. Look for concrete
failures, repeated proof runs, waits and manual steps in the current task's evidence.
Choose a bounded remedy that preserves requirements and other sessions' changes.
Prefer fixing the tool to adding an instruction. A useful instruction needs an
observable trigger, evidence, an action, a real check and a retirement condition.

Apply the remedy within the existing authorized scope, verify it and automatically
create a local commit containing only the owned, checked changes after any required
independent review. Preserve staged work belonging to others and run native hooks.
The bundled `migliora apply` helper manages only its block in `AGENTS.md`; code
remedies use the project's ordinary workflow. Never bypass checks, publish or push
implicitly, change requirements automatically, or approve project memory proposals.
No actionable evidence means no change. Measure the effect at the next matching
trigger and remove ineffective rules through the same checked workflow.
