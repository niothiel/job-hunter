# Shared customize-grade-veracity loop with YES/NO stopping signal

**Status:** accepted

The resume pipeline's optimizer and customizer are merged into a single
implementation. The loop becomes:

```
step6_customize → step7_grade_resume → step8_veracity
       ↑                                       │
       └──────── repeat if useful ──────────────┘
              (customizer says YES)
```

Veracity moves inside the loop — it evaluates every version, not just the
final one. The separate `build_optimize_prompt` is eliminated; the shared
`build_customize_prompt` handles both first drafts and revisions.

The stopping signal is a YES/NO answer from the customizer: after editing
the resume, it answers "Could this be truthfully improved further?" If
NO, the loop ends. If YES, the loop continues to the next version.

## Context

The previous architecture had two separate implementations:

1. **Step 6 customize** — created the first tailored resume from the base
   resume, JD, and few-shot examples. Used `build_customize_prompt`.

2. **Step 8 optimize** — decided whether to continue the loop. Made a
   separate LLM call ("can you improve?") that returned a grade,
   improvement notes, and `can_improve` boolean. If YES, routed back to
   step6 with a different prompt (`build_optimize_prompt`) that omitted
   few-shot examples and JD-grade context.

The problems:

- **Two prompts for the same task.** The optimize prompt was a stripped-down
  version of the customize prompt. They diverged in content and produced
  different behavior for first drafts vs. revisions.

- **Veracity outside the loop.** Truthfulness review ran once, after the
  loop ended. A revision could introduce unverified claims that were only
  caught at the end — after the version was already selected.

- **Optimize made its own LLM call.** The customizer already knows whether
  it can improve the resume; asking again in a separate call is redundant.

- **Version history was implicit.** The pipeline tracked only the latest
  grade and one verification result. If a revision regressed, there was no
  way to fall back to an earlier version.

## Decision

### Shared implementation

`build_customize_prompt` is the single prompt builder for all versions.
The difference between first draft and revision is the **starting draft** —
v1 starts from the base resume, v2+ starts from the previous version's
output. Both get the same source documents, few-shot examples, and
feedback.

The prompt instructs the model to:
1. Edit the resume (making source-supported improvements from the feedback)
2. After editing, answer "Could this be truthfully improved further? YES or NO"

The YES/NO is the stopping signal. The orchestrator validates it:
- If the draft changed and the model said NO → the signal is ignored (the
  model made changes despite claiming none were needed)
- If the draft is byte-identical and the model said NO → valid stop
- If the draft is byte-identical and the model said YES → error (the model
  claimed improvements existed but didn't make them)

### Loop structure

```
START → step3_ingest → step4_grade_jd → step5_triage → step6_customize
                                                          ↓
                                              step7_grade_resume
                                                          ↓
                                              step8_veracity  (in-loop)
                                                          ↓
                                              step9_should_continue
                                                   ↓
                                          ┌─── YES ──→ step6_customize
                                          │            (next version)
                                          │
                                          └─── NO ───→ step10_final_veracity
                                                       (select + re-verify,
                                                        cascade on failure)
                                                          ↓
                                                   step11_finalize
                                                       (prune + route)
```

- **step6_customize** — shared implementation. Pre-copies the starting
  draft (base resume for v1, previous version for v2+), builds the prompt,
  calls the customizer with `permission_mode='dangerous'`, parses the
  YES/NO outcome, validates it against the actual file diff.

- **step7_grade_resume** — unchanged. Two-call decomposition (reasoning +
  scoring) produces the grade for the current version.

- **step8_veracity** — moved inside the loop. Two-call decomposition
  (classification + synthesis) produces the verified status for the current
  version. Renamed from step9 to step8 (the old step8_optimize is gone).

- **step9_should_continue** — pure routing node, no LLM call. Reads the
  customizer's YES/NO signal and the truthfulness override:

  - YES → route back to step6 (next version)
  - NO + verified → route to step10 (done)
  - NO + NOT verified → route back to step6 (truthfulness repair is
    mandatory — the override forces continuation)
  - Budget exhausted (3 versions) → route to step10 regardless

- **step10_final_veracity** — the post-loop truthfulness gate, its own
  stage (the in-loop review verifies each version to steer the loop; this
  one re-verifies the version actually selected to ship):
  - Candidates = versions with grade ≥ 9 AND verified = True, best first
  - The best candidate is re-verified with the full two-call check —
    always, even though it passed in-loop (verification is
    non-deterministic; the final check is an independent authoritative
    gate before anything reaches ready/)
  - If it fails, the next-best candidate is tried; the cascade continues
    until one passes or all fail
  - `selected_version` is set only on a confirmed pass — a non-None value
    means "verified winner"
  - If every candidate fails → no winner; step11 holds the job for human
    review (a truthfulness failure is never a silent rejection)

- **step11_finalize** — routes on the confirmed winner:
  - Winner confirmed → prune non-selected resume files (only the winning
    version ships to `4_ready/`; pruning happens only after the final
    gate so fallback candidates survive the cascade)
  - Never ships unverified: a `selected_version` without a passing
    `final_verification` is an inconsistent state (legacy checkpoint,
    --step misuse) and is held for human review
  - No winner → `6_rejected/[RESUME]`, unless a truthfulness failure
    occurred anywhere (in-loop `verified=False` or `final_failed_versions`)
    → held for human review

### Version tracking

`JobState` gains a `version_history` field — a list of version records:

```python
class ResumeVersion(BaseModel):
    version: int                    # 1, 2, 3
    grade: float                    # from grading
    verified: bool                  # from veracity
    draft_hash: str                 # sha256[:12] of draft content
    path: str                       # filesystem path to this version
    truthfulness_issues: list[str]  # claims that failed verification
```

Each version's grade and veracity result are recorded independently. The
best passing version is selected from this history, not inferred from
filename scores.

### Truthfulness override

A veracity failure (`verified=False`) is treated differently depending on
where it occurs:

- **During the loop:** If the customizer says NO but the latest version
  fails veracity, the override forces another revision (if budget remains).
  The resume is not considered finished until a version is verified.

- **After the loop:** The final gate (step10_final_veracity) re-verifies
  the selected version independently — in-loop verification steers the
  loop; the final check guards what actually ships. If the selected
  version fails it, the next-best passing version is tried; only when
  every candidate fails is the job held for human review. A veracity
  failure is not silently skipped — it means the model produced
  unverifiable claims, which is a signal that something is wrong with
  the source material or the model's behavior.

### Backward compatibility

Existing JobState records may not have `version_history`. The code handles
this gracefully:

- If `version_history` is empty, treat the current `customized_resume`,
  `resume_grade`, and `veracity_verified` as v1.
- The next loop pass creates v2 and appends to `version_history`.
- No migration script needed — the field defaults to an empty list.

## Considered Options

- **Arm A — validated no_change:** The customizer returns
  `{"outcome":"revised"}` or `{"outcome":"no_change"}` after editing.
  `no_change` means the draft is byte-identical. The pilot showed this
  signal **never fires** — the customizer always finds something to revise.
  In all 3 test cases, Arm A used the full 3-version budget. Worse, the
  aggressive revision strategy broke truthfulness in the omission case
  (v3 achieved grade 10.0 but failed veracity, producing no passing
  version). Rejected for inefficiency and truthfulness risk.

- **Arm B — YES/NO after editing (chosen):** The customizer edits the
  resume, then answers "Could this be truthfully improved further? YES or
  NO." The pilot showed this is efficient — it stopped after 1 revision in
  all 3 cases, produced equal or better passing results in 2 of 3 cases,
  and never produced an unverified final version. One premature stop
  (omission: 9.67 vs probe's 10.0) still produced a passing version.
  Chosen for efficiency and result quality.

- **Arm C — pre-edit ask:** Before editing, the customizer is asked
  "Can you improve? YES or NO." If NO, skip the edit. The pilot showed
  this always says YES — the customizer always believes it can improve.
  The pre-edit ask adds an extra call (2s) without changing behavior.
  Rejected as a no-op that adds cost.

- **Budget-only (no signal):** Always run the full 3-version budget, select
  the best passing version. Simplest and most predictable. Same as Arm A
  in practice (since no_change never fires), but without the overhead of
  asking. Rejected because it always pays for 3 versions even when the
  customizer knows it's done.

## Consequences

- **One shared prompt builder.** `build_optimize_prompt` is deleted.
  `build_customize_prompt` handles both first drafts and revisions. The
  prompt size is larger for revisions (includes few-shot examples), but
  the pilot showed this doesn't degrade output quality — Arm B's revisions
  maintained or improved grades.

- **Two veracity stages.** Every version is verified inside the loop
  before being considered for selection, catching truthfulness
  regressions immediately; and the selected version is re-verified by a
  final gate after the loop, guarding what actually reaches ready/. The
  cost is one extra veracity call per version plus two calls (the
  classification + synthesis pair) per candidate checked at the final
  gate — 4 calls for the common single-version pass.

- **Fewer LLM calls on average.** The YES/NO signal stops the loop early
  in most cases. In the pilot, Arm B used 2 versions vs. 3 for Arm A —
  saving ~5 calls per job (1 customize + 4 evaluation). Over 50 jobs per
  day, this saves ~250 LLM calls.

- **Explicit version history.** The `version_history` field makes the
  loop's behavior auditable. Each version's grade, veracity, and path are
  recorded. If a revision regresses, the earlier version is still
  available as a candidate.

- **Truthfulness is a hard gate.** Any version that fails veracity is
  flagged. During the loop, it forces a repair. After the loop, it
  triggers human review. This is stricter than the previous behavior
  (which silently skipped unverified versions).

- **The optimize node's LLM call is eliminated.** Step8's "can you
  improve?" call is replaced by the customizer's YES/NO output. This
  removes one LLM call per iteration and eliminates the risk of the
  optimizer and customizer disagreeing about whether improvement is
  possible.

- **Premature stop risk.** The YES/NO signal can stop the loop before
  the best possible version is found. In the pilot, this happened once
  (omission: stopped at 9.67, probe found 10.0). The mitigations are:
  (1) the truthfulness override still forces repair if needed,
  (2) the best passing version is still selected from history, and
  (3) the premature stop still produced a passing version — it wasn't
  a failure, just a missed optimization opportunity.

## Evidence

The stopping-policy pilot compared three approaches on three private-data
cases:

| Case | A (no_change) | B (YES/NO) | C (pre-edit ask) |
|---|---|---|---|
| Omission (fixable gap) | No passing version | **9.67** (probe: 10.0) | 10.0 |
| Truthfulness (inflated claim) | 9.83 | **10.0** | 9.5 |
| Irreducible (quantum gap) | 10.0 | **10.0** | 9.94 |

Arm B won or tied in all 3 cases, stopped early in all 3 cases, and never
produced an unverified final version. Arm A never returned no_change and
broke truthfulness once. Arm C's pre-edit ask never said NO.

Total pilot calls: 122 (under the 150 budget).
