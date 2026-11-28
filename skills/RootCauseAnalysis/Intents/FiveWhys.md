# FiveWhys

A Five Whys causal chain (a tree where branches exist) from a precise problem statement down to actionable systemic root causes, with owned corrective actions.

## Ideal State

- A precise, measurable problem statement leads through evidenced direct causal links to actionable systemic causes.
- The chain includes every supported branch, and a bottom-to-top "because … therefore" reading has no logical gaps.
- The number of whys is not fixed; where more than roughly five whys signal a switch between causal chains, that is represented as a tree.
- Converging branches are identified as causes whose correction closes multiple failure paths.
- Corrective actions name owners and deadlines and address recurrence rather than vigilance.
- When the optional Five Hows is used, prevention reasoning extends until the proposed fix is as well supported as the diagnosis.

## Anti-criteria

- The chain never stops at "operator error" nor at an unchangeable abstraction.
- Unknown mechanisms are never filled with extra invented whys; they require evidence or domain expertise.

## Output format

```
🔍 5 WHYS ANALYSIS: [problem, 12 words]

PROBLEM: [precise statement]

CHAIN:
- WHY 1: [cause]
- WHY 2: [cause of WHY 1]
- WHY 3: [cause of WHY 2]
- WHY 4: [cause of WHY 3]
- WHY 5: [cause of WHY 4 — root]

BRANCHES: [if any]
- At WHY N:
  ├─ Branch A: ...
  └─ Branch B: ...

ROOT CAUSE(S): [systemic, actionable]
- [Cause 1]
- [Cause 2]  ← if branches converged

CORRECTIVE ACTIONS:
- [Specific action — owner — deadline]
- [Specific action — owner — deadline]

VALIDATION (read forward):
Because [root], therefore [WHY 4], therefore [WHY 3], ..., therefore [problem].
```

## References

Use [FaultTree.md](FaultTree.md) for safety-critical combinations or probability estimates, [KepnerTregoe.md](KepnerTregoe.md) for context-specific deviations. Crossing service boundaries may require the branching evidence graph in [Foundation.md](../Foundation.md), or Fishbone inside Postmortem.

This intent nests within [Fishbone.md](Fishbone.md) category branches and [Postmortem.md](Postmortem.md) contributing threads. Escalate structural branching to SystemsThinking/Iceberg.
