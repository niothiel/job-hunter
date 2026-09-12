# Out-of-range score clamping (clamp + warn + flag)

**Status:** accepted

When the LLM returns a grade outside [0, 10] (e.g. 10.3), the pipeline
clamps it to the valid range, logs a validation warning, and records the
raw value in the justification field for human review. The call is treated
as a transport success with a validation failure — not a generic failure.

## Context

Overnight real-LLM evaluation on 2026-09-11 observed a JD grading call
that returned `grade: 10.3`. The model responded with valid, parseable
JSON — the transport succeeded — but the score violated the semantic
constraint `[0, 10]`. The original code rejected this as a generic
`RuntimeError`, losing the successful call and forcing a retry.

We distinguish three outcomes:
- **Transport success**: `devin -p` returned output, model responded.
- **Validation failure**: output parsed structurally but violates semantic
  constraints (e.g. grade > 10).
- **Transport failure**: no output, timeout, subprocess error.

The 10.3 grade was a transport success + validation failure. Recording it
as a transport failure was wrong — it wasted a successful model call and
made the failure look like an infrastructure issue.

## Considered Options

- **Reject and retry (previous behavior):** Raise `RuntimeError` on
  out-of-range grades. The retry mechanism re-runs the call, hoping for an
  in-range score. Rejected because it wastes a successful model call (the
  model did produce valid JSON — just with an out-of-range number) and
  makes the failure indistinguishable from a transport failure.

- **Silent clamp:** Clamp to [0, 10] without recording the raw value or
  logging a warning. Rejected because it hides the signal that the model
  misbehaved. Recurring out-of-range values would be invisible.

- **Clamp + warn + flag (chosen):** Clamp to [0, 10], log a validation
  warning, and record the raw value in the justification field as
  `[VALIDATION: raw grade X clamped to Y]`. The clamped grade is used for
  pipeline routing. The raw value is preserved for review. The call is not
  counted as a failure.

## Consequences

- **Out-of-range grades no longer fail the pipeline step.** The clamped
  grade is used for routing (e.g. a 10.3 JD grade becomes 10.0, which
  proceeds to customization). This is the intended behavior — the model's
  intent was clearly "maximum score" and the 0.3 excess is a formatting
  error, not a substantive misjudgment.

- **The raw value is preserved in the justification.** Reviewers can
  audit how often the model produces out-of-range values and investigate
  patterns.

- **The warning is logged via `structlog`.** The warning includes the
  raw grade, the clamped grade, and the job slug for diagnosis.

- **Applies to both JD grading (step4) and resume grading (step7).** Both
  steps clamp before creating the Pydantic model (`JdGrade` / `ResumeGrade`),
  which would otherwise reject the out-of-range value via `Field(ge=0, le=10)`.
