# Postmortem

**Contents:** Ideal State · Anti-criteria · Output format · References

A blameless postmortem record for an incident: UTC timeline, contributing factors, defensive-layer map, and strength-rated owned action items.

## Ideal State

- A blameless incident record meets the Output format template, separating proximate, systemic, detection, and response factors with evidence and independent corrective actions.
- Its chronological UTC timeline captures available signals, actions, communications, and the information at each decision point.
- The chronology existed and was checked before any hypothesis was ranked, so no ranked hypothesis rests on an event missing from it.
- The defensive-layer map records each existing defense and its hole, including active failures and latent conditions.
- Fixes address dormant weaknesses that permit a class of incident, not just its latest trigger.
- The record reconstructs what responders saw, knew, and intended.
- Blamelessness still requires each action to have a specific person as owner, a date, completion verification, and a strength rating; if leading actions rely on training or reminders, the systemic diagnosis is revisited.
- The record links its incident record, and its Follow-Up carries a tracking link (or a marked placeholder) for the action items and a dated 30-day review item that checks both action completion and prevention of the failure class.

## Anti-criteria

- The record is never organized around a favored cause.
- The review is never mixed with performance conversations, and it carries no personal judgments or punitive questions.

## Output format

Action strength: Eliminate (strongest), Force function or Automation (strong), Simplify or Standardize (medium), Training (weak), Reminder/documentation (weakest).

```
# Postmortem: [Incident title]

**Date:** YYYY-MM-DD
**Authors:** [list]
**Status:** Draft / Final
**Classification:** Internal / Confidential
**Incident record:** [link]

## Summary

[1-2 paragraph executive summary. Non-technical stakeholders should be able to read this.]

## Impact

- User-facing: [N users affected, M% of traffic, X minutes]
- Revenue: [$ impact if calculable]
- Data: [any loss, corruption, or exposure]
- On-call: [who paged, for how long]

## Timeline

**All times UTC.**

- `23:47` — Deploy D-1234 pushed to production
- `23:49` — Traffic shift to new version
- `23:51` — First p99 latency alert fires
- `23:52` — On-call paged (Alice)
- `23:53` — Alice acknowledges; begins investigation
- `23:55` — Alice identifies elevated 500s in payments service
- `23:58` — Alice initiates rollback
- `00:01` — Rollback complete; metrics recover
- `00:05` — Incident closed

## Contributing Factors

### Proximate Cause
[What immediately triggered the incident]

### Systemic Factors
[The conditions that made the proximate cause possible or likely]

1. **[Factor 1]**
   - Evidence: ...
   - Why it existed: ...

2. **[Factor 2]**
   - ...

### Detection Gaps
[Why didn't we know sooner?]

### Response Gaps
[Why did recovery take as long as it did?]

## Swiss Cheese Analysis

Defensive layers that existed and the holes in each:

- **Layer 1 — CI gate:** Tests passed; hole — no load tests, didn't cover this query pattern
- **Layer 2 — Canary:** Not used for this deploy; hole — deploy process doesn't require canary for payments service
- **Layer 3 — Pre-deploy runbook:** Runbook doesn't include p99 check; hole — runbook predates p99 monitoring
- **Layer 4 — Monitoring:** p99 alerting did fire but took 2 minutes; hole — alert evaluation window too long

## What Went Well

[Always include. Practices that worked. Morale fuel.]

- Rollback procedure ran cleanly
- On-call response time was under SLA
- Cross-team communication in incident channel was clear

## What Went Poorly

[Blameless. Process failures, not person failures.]

- Deploy process allowed full-traffic cutover without canary
- Runbook was out of date
- Alert evaluation window was too long for this class of problem

## Action Items

| # | Action | Strength | Owner | Deadline | Verification |
|---|--------|----------|-------|----------|--------------|
| 1 | Require canary (10/50/100 with p99 gate) for payments deploys | Automation (Strong) | Platform-eng | Apr 30 | PR merged + canary used on next 3 deploys |
| 2 | Update pre-deploy runbook with p99 check | Standardize (Medium) | Payments-oncall | Apr 18 | Runbook diff merged; team review confirms |
| 3 | Reduce p99 alert evaluation window from 5m to 1m for payments | Simplify (Medium) | SRE | Apr 22 | PagerDuty config diff merged |
| 4 | Backfill missing index | Eliminate (Strongest) | Payments | Today | Index exists; query plan confirms use |
| 5 | Add EXPLAIN ANALYZE to migration PR template | Force function (Strong) | Platform-eng | Apr 25 | PR template updated; next 3 migrations include it |

## Lessons Learned

[What do we know now that we didn't know before?]

- Deploy tests don't cover database query plan regression — we were relying on a latent assumption.
- p99 alerts fired but not fast enough to prevent user impact. Alert latency matters as much as alert accuracy.
- The migration review process was the structural hole — not the engineer, not the deploy.

## Follow-Up

- [ ] Action items tracked in [ticket system link]
- [ ] 30-day review on YYYY-MM-DD: confirm every action is complete and the failure class has not recurred
- [ ] Pattern check: are similar incidents occurring across other services? (escalate to SystemsThinking if yes)
```

## References

See [Foundation.md](../Foundation.md) for cross-service clock and evidence limitations.

### Investigation Routing

| Contributing thread | Intent |
|---|----|
| Clear single-thread mechanism | [FiveWhys.md](FiveWhys.md) |
| Multiple suspected categories | [Fishbone.md](Fishbone.md) |
| Works on X, not Y | [KepnerTregoe.md](KepnerTregoe.md) |
| Complex multi-path failure | [FaultTree.md](FaultTree.md) |

Recurring patterns feed SystemsThinking/Iceberg or FindArchetype; findings feed proactive FMEA.
