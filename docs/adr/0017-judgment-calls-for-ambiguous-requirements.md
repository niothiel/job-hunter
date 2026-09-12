# Judgment calls for ambiguous requirement classification

**Status:** accepted

The grading reasoning protocol's rigid "if ambiguous, classify as Core"
fallback is replaced with judgment-based classification. When the model
encounters a requirement phrase not in the standard Core or Preferred
lists, it uses its judgment to classify the requirement and records the
decision in a new `judgment_calls` field for human review. Known phrases
("highly desirable," "desired," "strongly preferred") are added to the
Preferred examples list.

## Context

Overnight real-LLM evaluation on 2026-09-11 observed a 2.8-point swing in
resume grading for the same JD across 3 runs (Oracle senior director:
5.5, 8.3, 8.3). Root cause: the JD said "Familiarity with networking
fundamentals and configuration management is **highly desirable**." This
phrase is in neither the Core list ("required," "must have," "minimum,"
"essential," "Basic Qualifications") nor the Preferred list ("preferred,"
"nice to have," "bonus," "would be a plus").

Run 1 applied the fallback ("if ambiguous, classify as Core") → Core/GAP
→ ceiling capped at 8.0 → grade 5.5. Runs 2-3 classified it as
Preferred/PARTIAL → ceiling stayed 9.0 → grade 8.3. The correct
classification is Preferred — "highly desirable" signals preference, not
requirement.

Research into HR/recruiting terminology confirmed:
- University of the Incarnate Word HR guide: "Preferred Qualifications —
  should identify **highly desirable** or 'nice to have' education, skills,
  or certifications."
- Serent Capital executive recruiting: three-tier system (must-have,
  highly desirable, nice-to-have). In our two-tier system, "highly
  desirable" maps to Preferred.
- QASkills on LLM evaluation: "Use human review to define the standard,
  resolve consequential ambiguity, and audit automated graders."

## Considered Options

- **Add phrases to Preferred list only:** Add "highly desirable," "desired,"
  "strongly preferred" to the Preferred examples. Keep "if ambiguous,
  classify as Core" as the fallback. Rejected because it only fixes known
  phrases — hundreds of other ambiguous phrasings exist ("important," "key
  qualifications," "we're looking for") and the rigid fallback would still
  produce false penalties for those.

- **Add phrases + flip fallback to Preferred:** Add the phrases AND change
  the fallback from "classify as Core" to "classify as Preferred." Rejected
  because a blanket "classify as Preferred" default is not always correct
  — some ambiguous phrases do signal requirements. The model should use
  judgment, not a blanket default.

- **Add phrases + judgment + `judgment_calls` field (chosen):** Add the
  known phrases to the Preferred list. Replace the rigid fallback with:
  "If the phrase is not in either list, use your judgment to classify it
  as Core or Preferred, and record it in `judgment_calls` for human
  review." The `judgment_calls` field captures the requirement, the
  ambiguous phrase, the chosen classification, and the reasoning.

- **Remove fallback entirely:** Force the model to always pick a tier
  with no guidance for unknown phrases. Rejected because the model might
  hallucinate a tier for truly ambiguous cases. The judgment call field
  with reasoning is safer than no guidance.

## Consequences

- **New `JudgmentCall` model in `state.py`.** Fields: `requirement`,
  `phrase`, `classified_as` ("core" or "preferred"), `reason`. Added to
  both `JdGrade` and `ResumeGrade` as `judgment_calls: list[JudgmentCall]`.

- **The `judgment_calls` field is part of the grading output schema.**
  Call 1 (reasoning) produces it. Call 2 (scoring) passes it through
  unchanged. The pipeline stores it in the grade JSON for auditability.

- **Human review loop.** Reviewers can periodically inspect
  `judgment_calls` entries, identify common ambiguous phrases, and promote
  them to the explicit Core/Preferred lists in the protocol files. Over
  time, the judgment call set shrinks.

- **All 3 reasoning protocols updated.** `grading-reasoning-protocol.md`,
  `jd-grading-reasoning-protocol.md`, and `grading-protocol.md` (single-call
  debug version) all have the new Preferred phrases, the judgment call
  instruction, and the `judgment_calls` field in the JSON output format.

- **Both scoring protocols updated.** `grading-scoring-protocol.md` and
  `jd-grading-scoring-protocol.md` pass through `judgment_calls` unchanged.

- **Prompt builders updated.** `step4_grade_jd.py` and
  `step7_grade_resume.py` mention `judgment_calls` in the output format
  hint in the prompt.

- **The Oracle 5.5 outlier should not recur.** "highly desirable" is now
  explicitly in the Preferred list. Unknown phrases will be classified by
  judgment and recorded, not blindly classified as Core.
