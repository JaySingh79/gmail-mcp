---
name: RootCauseAnalysis
evals:
  manual:
    reason: "a causal diagnosis must be judged against incident evidence, not merely the completeness of a chosen framework"
    falsifier: "the reviewer finds a proposed root cause unsupported by evidence or a corrective action cannot address the stated failure mode"
metadata:
  short-description: "Investigate incidents and recurring failures with causal methods such as Five Whys, fault trees, and postmortems. Use SystemsThinking for feedback loops."
version: 1.1.1
description: "Structured incident investigation using Five Whys, Fishbone, blameless Postmortem, Fault Tree, Kepner-Tregoe, and FMEA — traces failures to systemic root causes rather than blaming humans. USE WHEN root cause, RCA, 5 whys, fishbone, postmortem, incident analysis, fault tree, why does this keep failing, blameless, recurring bug. NOT FOR systemic loops (use SystemsThinking)."
---

# RootCauseAnalysis Skill

RootCauseAnalysis investigates incidents and recurring failures with causal methods (Five Whys, Fishbone, blameless Postmortem, Fault Tree, Kepner-Tregoe) and traces them to systemic root causes rather than to a person to blame. Apollo/RealityCharting and FMEA are supporting methods documented in `Foundation.md` and `MethodSelection.md`, not separate intents.

## Ideal State

- The method fits the failure class, as the situation table under Intent Routing maps it, and a non-trivial incident is written as a Postmortem with the other methods as its investigation tools.
- Every diagnosis is evidence-backed and blameless, with 3+ actionable systemic contributing factors rather than a single blame target. It ends at causes that are both systemic and within authority to change, never at a person to blame, and each corrective action prevents a class of recurrence rather than the latest trigger.
- Every conclusion that rests on a fact the request supplied cites that fact, checked at its source, and a caller's hypothesis is never taken as fact.
- Decisions are judged using the information available at the time.
- What the evidence cannot support is disclosed as a gap or a hypothesis, never filled with invented causes, probabilities or comparisons, and never padded with invented factors to meet the 3+ target.
- The analysis delivered is finished; no sub-work is still running when it is handed over.

## Intent Routing

| Intent | Trigger | File |
|----------|---------|------|
| **FiveWhys** | "5 whys", "five whys", quick causal chain, ask why until root | `Intents/FiveWhys.md` |
| **Fishbone** | "fishbone", "ishikawa", categorized cause map, 6 M's / 4 P's / 8 M's | `Intents/Fishbone.md` |
| **Postmortem** | "postmortem", "incident review", "blameless postmortem", production incident | `Intents/Postmortem.md` |
| **FaultTree** | "fault tree", "fta", top-down deductive, safety-critical, AND/OR logic | `Intents/FaultTree.md` |
| **KepnerTregoe** | "kepner tregoe", "is/is-not", "what changed", distinction analysis, subtle defects | `Intents/KepnerTregoe.md` |

| Situation | Preferred intent |
|-----------|---------------------|
| Single-thread incident, one clear failure point | **FiveWhys** |
| Multiple suspected categories (people, process, tools) | **Fishbone** |
| Production outage or security incident, needs formal review | **Postmortem** |
| Complex multi-path failure, safety-critical, need Boolean logic | **FaultTree** |
| Subtle defect, hard to reproduce, "why here and not there?" | **KepnerTregoe** |

| File | Read when |
|------|-----------|
| `MethodSelection.md` | choosing, combining or switching methods: decision flow, method comparison, domain map, speed-versus-thoroughness |
| `Foundation.md` | evidence crosses service boundaries, or the work uses Apollo evidence graphs or FMEA prioritization |

Standalone; no required skill dependency. Use SystemsThinking (Iceberg, FindArchetype) for recurring structural causes, FirstPrinciples to decompose a contributing factor, RedTeam to stress-test remediations, and Science for investigation hypotheses.

## Data & Config

User customizations live at `<configRoot>/LIFEOS/USER/CUSTOMIZATIONS/SKILLS/RootCauseAnalysis/` (`configRoot` is `$CLAUDE_CONFIG_DIR` when set, else `~/.claude`). When that directory exists, its `PREFERENCES.md`, configurations and resources apply and override the defaults here; when it is absent, the defaults stand.

## Gotchas

- Start from the facts and paths the request supplies and read those paths; do not search for them again. Any search names the paths it covers, never the whole home tree or ~/Projects. This scopes where to look, never what may be investigated.

## Examples

**Example 1: Production outage**
```
User: "Payments threw 500s for ten minutes after last night's deploy. Write the postmortem."
→ Postmortem intent, with FiveWhys or Fishbone on each contributing thread
→ A blameless record with a UTC timeline, defensive-layer map and strength-rated owned actions
```

**Example 2: Works here, not there**
```
User: "The export job fails only on the EU cluster and only on Mondays. Why?"
→ KepnerTregoe intent
→ An IS/IS-NOT matrix, the change that explains every IS without predicting the IS-NOTs, and a test to confirm it
```

**Example 3: Quick causal chain**
```
User: "5 whys on why the nightly backup keeps missing its window"
→ FiveWhys intent
→ An evidenced chain to systemic causes, read forward with no gaps, and owned corrective actions
```
