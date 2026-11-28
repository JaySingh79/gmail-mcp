# KepnerTregoe

A Kepner-Tregoe IS/IS-NOT analysis of a deviation from known-good, ending in the surviving cause and a plan to verify it.

## Ideal State

- Expected and actual performance are explicit.
- Comparable IS/IS-NOT observations span What, Where, When, and Extent, with distinctions, candidate changes, causal mechanisms, and tests against both sides.
- A surviving hypothesis explains every IS entry without predicting failures in observed IS-NOT cases, and remains unconfirmed until tested.
- The change window runs from last-known-good to first-observed-bad, extended for slow drift, and candidate changes include cron/batch schedules, calendar events, data volume, personnel coverage, and external dependencies.
- Verification establishes the deviation with the cause present and its absence when removed, ideally by reverting in a test environment.
- Missing baseline or IS-NOT observability is reported as a limitation.

## Anti-criteria

- "No deploy" is never taken to mean "nothing changed".
- A shared condition alone is never offered as the explanation of the distinction, though it can interact with a changed condition.
- Missing baseline or IS-NOT observability is never a license to invent comparisons.

## Output format

```
🔬 KEPNER-TREGOE ANALYSIS: [deviation]

DEVIATION:
- Expected: [...]
- Actual: [...]

IS/IS-NOT MATRIX:

| Dimension | IS | IS NOT | Distinction | Change |
|-----------|----|----|------|--------|
| What      | [specific]  | [similar OK] | [diff]      | [change in that area] |
| Where     | [location]  | [elsewhere OK] | [diff]      | [change in that location] |
| When      | [time]      | [other times OK] | [diff]   | [change at/before] |
| Extent    | [magnitude] | [baseline] | [diff]      | [change in scale] |

DISTINCTIONS:
- [D1]
- [D2]
- [D3]

CHANGES (candidates):
- [C1] — in area of [D1]
- [C2] — in area of [D2]

POSSIBLE CAUSES:
- [Cause A] — from change [C1] — mechanism: [...]
- [Cause B] — from change [C2] — mechanism: [...]

TESTING:
- Cause A:
  - Explains IS? ✓ / ✗ — [why]
  - Consistent with IS NOT? ✓ / ✗ — [why]
- Cause B: ...

SURVIVING CAUSE: [the one that explains all]
VERIFICATION PLAN: [how we'll confirm]
```

## References

[Postmortem.md](Postmortem.md) invokes KT for unclear deviations. [FiveWhys.md](FiveWhys.md) investigates why the identified change escaped defenses; [Fishbone.md](Fishbone.md) organizes remaining categories or provides an alternative when no baseline exists. Observability must support both IS and IS-NOT evidence. See [MethodSelection.md](../MethodSelection.md) for method switching. KT is the wrong tool for a consistent, obvious failure, a complete system failure, or time-critical triage.
