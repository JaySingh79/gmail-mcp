# RootCauseAnalysis — Method Selection Guide

## Decision Flow

```
Is this an incident or a defect?
│
├─ Incident (production outage, security event, data loss)
│    │
│    └─ Use Postmortem as wrapper
│         ├─ Single thread, clear mechanism → 5 Whys inside
│         ├─ Multiple suspected categories → Fishbone inside
│         ├─ "Works here not there" subtle → Kepner-Tregoe inside
│         └─ Safety/security-critical → Fault Tree Analysis inside
│
└─ Defect (recurring bug, quality drift, process failure)
     │
     ├─ Simple, single-thread → 5 Whys
     ├─ Multi-category or brainstorm needed → Fishbone + Pareto
     ├─ Deviation from known-good → Kepner-Tregoe IS/IS-NOT
     ├─ Novel, never-happened-before → Apollo/RealityCharting
     └─ Complex interacting failures → Fault Tree
```

## Quick Decision Table

| Criterion | 5 Whys | Fishbone | FTA | Apollo | KT |
|-----------|--------|----------|-----|--------|-----|
| Problem complexity | Simple-moderate | Moderate-complex | Complex-very complex | Moderate-complex | Moderate |
| Causal structure | Linear (single chain) | Multi-category, parallel | Branching, probabilistic | Branching, evidence-based | IS/IS-NOT deviation |
| Team involvement | Solo or small team | Group brainstorm | Engineers + analysts | Formal panel | Solo or small team |
| Time available | Minutes-hours | Hours | Days-weeks | Hours-days | Hours |
| Safety-critical | No | No | Yes | Yes | No |
| Quantitative probability needed | No | No | Yes | No | No |
| Good for novel failures | Moderate | Yes | Yes | Yes | Moderate |
| Defensible for regulatory | No | Partial | Yes | Yes | Partial |
| Outputs | Causal chain + fix | Category map + Pareto | Cut sets + probabilities | Causal graph + evidence | Distinction + change |

## Combining and Switching

Fishbone combines breadth with Pareto prioritization and FiveWhys depth; KT can hand its identified change to FiveWhys to investigate why defenses missed it. Safety analysis pairs FTA with FMEA; see [Foundation.md](Foundation.md) for RPN/AP caveats and Apollo's evidence-graph contract. Apollo and FMEA are supporting methods, not separate intent files here.

Switch from FiveWhys to Fishbone when parallel branches outgrow a chain; switch back when the category is known and depth is missing. Without probability data, FTA loses its quantitative advantage; Fishbone plus FiveWhys may suffice. Without a known-good baseline, use Apollo or Fishbone instead of KT. Incident size alone is not a reason to skip the Postmortem wrapper.

## Method-to-Domain Map

| Domain | Primary method | Secondary |
|--------|----------------|----------|
| Production software outage | Postmortem + 5 Whys | Swiss Cheese, Fishbone |
| Distributed systems failure | Postmortem + Apollo | FTA |
| Security incident | Postmortem + Swiss Cheese | KT for subtle defects |
| Manufacturing defect | Fishbone + Pareto | 5 Whys |
| Intermittent / environment-specific | Kepner-Tregoe | 5 Whys |
| Safety-critical engineering | FTA | FMEA, Apollo |
| Pre-launch risk analysis | FMEA (proactive) | FTA |
| Process/org failure | Fishbone (4 P's) | 5 Whys |
| Regulatory investigation | Apollo / RealityCharting | FTA |

## Speed vs. Thoroughness Tradeoff

| Situation | Method |
|-----------|--------|
| Time pressure — 10 minutes | Quick 5 Whys |
| 1 hour | 5 Whys + Fishbone |
| Half day | Postmortem + multiple methods |
| Days | Postmortem + FTA + FMEA |
| Regulatory deadline | Apollo with full evidence |
