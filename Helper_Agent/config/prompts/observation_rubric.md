# Observation rubric

Apply this order to the supplied packet only:

1. Verify `run_id`, attempt provenance, source references, mission stage, and
   terminal class before interpreting symptoms.
2. Build the timeline from structured fields first and bounded log excerpts
   second. A missing field is not a zero value or a negative result.
3. Treat typed blockers as first-class evidence. Preserve their recorded
   classification unless a conflicting packet field is quoted explicitly.
4. Distinguish the terminal event, preceding symptoms, and suspected root
   cause. Co-occurrence and sequence alone do not prove causality.
5. Use `bug` only for evidence of incorrect deterministic behavior,
   `intended_difficulty` for genuine bounded supply or capacity pressure,
   `operational` for lifecycle or evidence-collection faults, and `unclear`
   when the packet cannot decide among them.
6. Record successful controller stages and reusable workflows even when the run
   later fails. Do not claim a workflow succeeded without an explicit completed
   state or equivalent evidence.
7. Treat a repeated signature as repeated only when supplied casebook evidence
   establishes it. Never infer repetition from one packet.
8. Calibrate confidence: high requires direct structured evidence, medium may
   join consistent independent fields, and low covers incomplete or ambiguous
   evidence.
9. Prefer a precise missing observation and next probe over a speculative
   cause. The probe must remain observational and non-mutating.
10. Never interpret absent user feedback as confirmation, rejection, or
    permission. Recommendations identify categories, not implementations.
