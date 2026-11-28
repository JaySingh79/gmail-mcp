# FaultTree

A fault tree for a precise top event: a Boolean gate tree, minimal cut sets, flagged common modes and mitigation tied to the cut sets.

## Ideal State

- A precise top event has a Boolean tree with explicit gates, bounded basic events, minimal cut sets, and mitigation tied to those cut sets.
- Quantitative estimates are included only where probability evidence exists, with the exposure basis and assumptions identified.
- Leaves stop at known or estimable rates, system boundaries, or the useful abstraction level.
- Human-error events are included without treating people as systemic root causes.
- Gate choice preserves the actual failure mechanism: order-sensitive failures are represented with Priority AND and conditioning events with Inhibit, with XOR available for exactly-one failures.
- Common modes (shared deployments, dependencies, power, and repeated basic events that can invalidate apparent defense-in-depth) are flagged explicitly.
- Mitigation targets the cheapest effective interruption of high-probability cut sets, prioritizing single-event failures and common modes, and the tree is reassessed after architecture changes.

## Anti-criteria

- Cut-set size alone is never presented as establishing safety.
- A sum of rare-event probabilities is never presented as the exact union probability; it is an approximation.
- Independent-input formulas are never applied to correlated inputs as if they held.

## Output format

The independent-input formulas are OR: 1 − ∏(1 − Pᵢ) and AND: ∏Pᵢ; both fail when inputs are correlated.

```
🌲 FAULT TREE ANALYSIS: [top event]

TOP EVENT: [...]

TREE:
  [Top]
    │
   (OR)
    ├── Intermediate A
    │     │
    │    (AND)
    │     ├── Basic 1  (P = 0.01)
    │     └── Basic 2  (P = 0.05)
    │
    ├── Intermediate B
    │     │
    │    (OR)
    │     ├── Basic 3  (P = 0.02)
    │     └── Basic 4  (P = 0.03)
    │
    └── Basic 5 (P = 0.001)

MINIMAL CUT SETS:
1. {Basic 1, Basic 2} — P = 5.0e-4
2. {Basic 3} — P = 0.02
3. {Basic 4} — P = 0.03
4. {Basic 5} — P = 1.0e-3

TOP EVENT PROBABILITY (approx): ~0.054 per event exposure

PRIORITY MITIGATION:
1. Basic 4 — highest single-event cut set; eliminate or reduce
2. Basic 3 — second-highest single; same logic
3. {Basic 1, Basic 2} — 2-event AND, acceptable if independence holds
   ⚠ Common-mode check: are 1 and 2 truly independent?

COMMON-MODE FAILURES (flagged):
- [Potential correlated failure between events, explained]

RECOMMENDED ACTIONS:
- Eliminate single-event cut sets first
- Harden against common-mode failures
- Add defenses that raise smallest cut-set size
```

## References

For non-trivial trees, use SAPHIRE, FaultTree+, or EC FTA; MOCUS supports cut-set calculation. Graphviz/d2/Mermaid only sketch trees; they do not calculate cut sets. Manual analysis is feasible below roughly 20 basic events.

[Postmortem.md](Postmortem.md) can invoke this intent when redundant defenses failed. Pair with FMEA; consult [Foundation.md](../Foundation.md) for its prioritization caveat. Repeated common modes feed SystemsThinking/CausalLoop or Archetypes. Use [MethodSelection.md](../MethodSelection.md) for alternatives when quantitative data or time is insufficient. FTA earns its cost when redundant defenses exist; with no redundancy the OR gate dominates and a simpler method does.
