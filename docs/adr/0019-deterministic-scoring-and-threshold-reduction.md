# Deterministic scoring + temporary threshold reduction

**Status:** accepted (deterministic scoring permanent; threshold reduction
temporary — revert to 8.0 once JD grading prompt is hardened against variance)

## Context

The legacy grading path made two LLM calls per grade: a reasoning call
(extract requirements, assess each criterion) and a scoring call (apply the
scoring formula to produce a numeric grade). The scoring formula is fully
specified arithmetic — ceilings, core score, preferred bonus, subjective
adjustment — so the second LLM call was paying an LLM to do deterministic
math. This had two problems:

1. **Cost + latency:** every grade paid for two LLM calls when one sufficed.
2. **Variance:** the LLM applying the formula introduced non-determinism
   into what should be a deterministic calculation (ADR-0015's
   out-of-range `10.3` was a symptom — the LLM mis-applied the formula).

Separately, the JD grade threshold for proceeding to resume customization
was historically 8.0. During A/B validation of the deterministic scorer, the
threshold was temporarily reduced to 7.0 to broaden the sample of JDs that
reach the customization loop (more data points for comparison). This is
temporary — the threshold should revert to 8.0 once the JD grading prompt is
hardened against variance (separate work, tracked via the variance harness).

## Considered Options

### Deterministic scoring

- **Keep two LLM calls (legacy):** LLM applies the formula. Rejected — the
  formula is arithmetic, not judgment; the LLM adds variance and cost
  without adding insight.

- **Score in code (chosen):** One LLM call produces per-criterion verdicts;
  Python applies the formula (`pipeline.infrastructure.scoring.compute_grade`).
  Eliminates the second call, eliminates formula-application variance, and
  makes the grade reproducible from the verdicts. The legacy path is
  preserved behind `deterministic_scoring=False` for A/B comparison.

- **Hybrid (LLM for edge cases, code for the rest):** Rejected — the formula
  has no edge cases that require judgment; it's fully specified.

### Threshold reduction

- **Keep 8.0:** Fewer JDs reach customization → smaller A/B sample. Rejected
  for the validation period only.

- **Reduce to 7.0 temporarily (chosen):** Broadens the sample. Revert after
  the JD grading prompt is hardened.

- **Reduce permanently:** Rejected — 8.0 is the right threshold for
  proceeding to customization (JDs in the 7.0–7.9 band are weak fits that
  don't justify the customization cost).

## Consequences

- `deterministic_scoring` config flag (default `true`) gates the two paths.
- The variance harness (`pipeline.helpers.variance_harness`) runs the same
  JD × N times in each mode to compare score distributions.
- `jd_grade_threshold` is 7.0 in config defaults and live config. **Revert
  to 8.0** once the JD grading prompt variance work is complete.
- The legacy two-call path is retained for A/B comparison and as a
  fallback; it will be removed once deterministic scoring is validated.
