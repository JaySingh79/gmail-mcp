# RootCauseAnalysis — Foundation

Use these caveats when the intents reference distributed evidence, Apollo, or FMEA.

- **Distributed evidence:** Correlate logs and traces across service boundaries using trace/correlation IDs. Clock skew or NTP problems can falsify apparent chronology. Insufficient observability limits causal conclusions; instrumentation should be the first corrective action when the incident cannot otherwise be diagnosed.
- **Apollo/RealityCharting:** For novel or cross-service failures, use an explicitly branching causal graph with observation or measurement supporting every cause node. Unevidenced nodes remain hypotheses. Bound the graph at actionable interventions.
- **FMEA:** Preserve the proactive handoff from incident findings to design or process failure modes. RPN = Severity × Occurrence × Detection (each 1–10) multiplies ordinal scales; it is not a ratio metric. The 2019 AIAG/VDA handbook replaced RPN as primary prioritization with Action Priority (H/M/L), weighting severity more heavily.
