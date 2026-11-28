# Fishbone

A fishbone (Ishikawa) cause map for a measurable problem, with nested causal depth, an impact-ranked Pareto table, the vital few and their verification plans.

## Ideal State

- A measurable problem has a category map with nested causal depth, an impact-ranked Pareto table, a vital-few shortlist, and verification plans.
- Candidate causes remain hypotheses until verified, and corrective actions correspond to verified causes.
- Each verification plan specifies the evidence needed, a test or historical query, and the prediction if the cause is real.
- The category set chosen (6M by default for software, or an alternative or custom set) is recorded, and an empty category is read as possibly indicating missing expertise rather than absence of causes.
- The Pareto table ranks impact, not causal validity: it shows each quantity and whether it was measured or estimated, and the vital few still get causal depth and verification.

## Anti-criteria

- Causes are never distorted to fit the chosen category set.
- Pareto percentages are never fabricated.

## Output format

Category sets: 6M (the software default) is People, Machine, Method, Material, Measurement, Environment. Alternatives: 4P (People, Process, Policies, Procedures); 8M (6M plus Management and Maintenance); 8P (Product/Service, Price, Place, Promotion, People, Process, Physical Evidence, Partners); or custom categories.

```
🐟 FISHBONE ANALYSIS: [problem]

PROBLEM: [precise statement]
CATEGORY SET: [6M / 4P / 8M / 8P / custom]

CAUSE MAP:

People:
- [Cause A1]
  └─ Why: [depth]
- [Cause A2]

Machine:
- [Cause B1]
- [Cause B2]

Method:
- [Cause C1]
  └─ Why: [depth]
    └─ Why: [depth]

Material:
- [Cause D1]

Measurement:
- [Cause E1]

Environment:
- [Cause F1]

PARETO (top causes by impact):
| Cause | Impact | Cumulative % |
|-------|--------|--------------|
| [C1]  | 45%    | 45%          |
| [B2]  | 25%    | 70%          |
| [A1]  | 15%    | 85%          |  ← 80% threshold above this line
| [D1]  | 10%    | 95%          |
| ...   | ...    | ...          |

VITAL FEW (primary focus):
1. [Cause] — verification plan: [...]
2. [Cause] — verification plan: [...]
3. [Cause] — verification plan: [...]

CORRECTIVE ACTIONS: [after verification, each verified cause gets an action]
```

## References

[FiveWhys.md](FiveWhys.md) supplies depth within category branches; Pareto supplies prioritization. [Postmortem.md](Postmortem.md) uses this map for contributing factors. Repeated structural causes feed SystemsThinking/Iceberg. See [MethodSelection.md](../MethodSelection.md) when changing methods.
