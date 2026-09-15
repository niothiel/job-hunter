# Reducing LLM-grading variance: research notes

Question: how to make `devin -p` LLM grading of JDs/resumes more deterministic
when temperature/seed can't be set — only prompt and call structure are under
our control. Current design: Call 1 extracts requirements + per-criterion
verdicts (DIRECT_HIT/ADDRESSED/PARTIAL/GAP); Call 2 computes the score via a
formula. Observed swing: 7.0–9.7 for identical input; threshold ~7.

The headline result of the research: **the biggest single lever available to us
is removing the LLM from the parts that don't need it** (scoring arithmetic,
requirement ordering), and **N-sample aggregation is the standard mitigation
when sampling parameters can't be controlled**.

---

## 1. Move deterministic computation out of the LLM (score Call 2 in code)

(a) **What it is.** The LLM produces judgments; a Python function applies the
scoring formula. This is the PAL pattern: let the model decompose and judge,
offload solving to a runtime.

(b) **Evidence.**
- Gao et al., *PAL: Program-aided Language Models* (ICML 2023,
  arXiv:2211.10435): LLMs "often make logical and arithmetic mistakes in the
  solution part, even when the problem is decomposed correctly." Offloading to
  an interpreter beat much larger models on all 13 tasks (+15 pts on GSM8K).
- Anthropic, *Demystifying evals for AI agents* (anthropic.com/engineering):
  code-based graders are "fast, cheap, objective, reproducible"; the design
  question is which parts of grading actually need a model.
- promptfoo LLM-as-judge guide: "stack deterministic checks with LLM judges" —
  deterministic layers first, LLM only for open-ended judgment.
- Local evidence: ADR-0015 exists only because the LLM scorer emitted 10.3 —
  a failure class that cannot exist in code.

(c) **Applicability.** `step4_grade_jd.py` / `step7_grade_resume.py` Call 2 is
pure formula application over Call 1's JSON (ceiling, core score, preferred
bonus, fixed adjustments). Implementing it in the orchestrator removes one LLM
call *and* one entire variance source. Keep Call 2's justification/summary
fields if wanted via a cheap template, or fold a one-line summary into Call 1.

(d) **Cost.** Negative — saves an LLM call per grade. ~100 lines of Python + unit
tests against the protocol formula.

---

## 2. Rubric design: anchored, low-cardinality, forced-choice verdicts

(a) **What it is.** Categorical verdicts with concrete per-level descriptors and
exemplars, not free numeric scores.

(b) **Evidence.**
- Stureborg et al., *LLMs are Inconsistent and Biased Evaluators*
  (arXiv:2405.01724): with wide scales (1-100) LLMs cluster scores, show
  round-number bias (90, 95 >> 92, 19), and leave most of the range unused;
  they also show low "inter-sample" agreement on identical inputs — i.e., a
  single numeric score is the least reliable output format.
- OpenAI evals `closedqa.yaml` (evals/registry/modelgraded/): canonical
  model-graded template uses **one criterion per eval, step-by-step reasoning,
  then a single Y/N letter** — easily-parsable forced choice.
- Prometheus 2 (arXiv:2405.01535): explicitly enumerated criteria with a
  description *per score level* + reference answer + verbal-feedback-before-score
  all measurably improve human correlation.
- LangSmith docs (llm-as-judge, create-few-shot-evaluators): inserting human
  corrections as few-shot examples is their primary judge-alignment lever.
  Anthropic "increase consistency" docs: examples beat abstract instructions.
- Rubric literature (Jonsson & Svingby 2007, cited by DnA-Eval): fewer, sharply
  defined levels → higher inter-rater reliability. Binary/3-level > 5 > 10 for
  consistency; promptfoo recommends binary pass/fail for release gates.

(c) **Applicability.** Our 4-level forced-choice per-criterion design is already
the right shape — the gap is anchoring: no worked examples, and the
ADDRESSED (0.5) vs PARTIAL (0.25) boundary is the fuzziest distinction in the
rubric. Concrete fixes: add 1-2 exemplar verdicts per level drawn from real
`judgment_calls` history; tighten or merge the two middle tiers; the verbatim
"Points" table should stay but each level gets an evidence bar ("specific,
quantified" is already there for DIRECT_HIT — extend to all four).

(d) **Cost.** Prompt edit only; near-free. Risk: longer prompt slightly raises
extraction noise — keep exemplars terse.

---

## 3. Decomposition: extract-then-score, separate what varies

(a) **What it is.** Split grading into stages so each call has one job and later
stages consume committed earlier output (already our two-call design; the
research supports pushing it one step further).

(b) **Evidence.**
- DnA-Eval (arXiv:2405.15329, COLING 2025): generate aspects → score per aspect
  → aggregate. Beats both direct-scoring and single-call CoT baselines;
  motivated by the pedagogy result that rubric decomposition "increases
  consistency" of evaluation.
- G-Eval (EMNLP 2023, arXiv:2303.16634): auto-generated CoT evaluation steps +
  form-filling → Spearman 0.514 on summarization, besting prior LLM metrics.
- FairEval MEC (Wang et al., ACL 2024, arXiv:2305.17926): forcing the evaluator
  to generate k=3 evidence items *before* scoring reduces positional bias and
  improves human alignment.
- OpenAI Cookbook, *Techniques to improve reliability*: "split complex tasks
  into simpler subtasks" and "explain before answering" are two of its core
  reliability techniques; OpenAI evals docs: "model grading works best... if
  we give them the ability to reason before making a judgment."
- Stureborg et al.: when multiple attribute labels are emitted in one
  generation, earlier scores anchor later ones — argues for independent
  per-criterion judgments (or at least a fixed canonical order).

(c) **Applicability.** Our split already prevents post-hoc justification of a
pre-chosen score (ADR-0011). The remaining coupled variance is *requirement-set
selection*: which/how many requirements are extracted changes the denominator
("Maximum 10 — pick the most important" is a nondeterministic sampler).
Two-step extension worth considering: Call 1a extracts requirements **verbatim,
in document order, no cap-or-curate**; Calls 1b assess against that *fixed* list.
This also makes per-criterion aggregation (§4) trivially alignable across runs.
Requirement ordering must be document order — fixes both cross-run comparability
and any within-prompt position effects.

(d) **Cost.** Splitting 1a/1b adds one call per grade; alternatively keep Call 1
whole but make extraction verbatim+ordered so a later aggregation step can match
criteria across runs.

---

## 4. Self-consistency: N samples + aggregation (the main lever without temp control)

(a) **What it is.** Run the judgment call N times; aggregate with majority vote
(categorical) or median (numeric). Report the spread as a health metric.

(b) **Evidence.**
- Wang et al., *Self-Consistency Improves CoT Reasoning* (arXiv:2203.11171,
  ICLR 2023): sampling diverse paths and majority-voting gave +17.9% GSM8K,
  +11.0% SVAMP etc. over greedy decoding; typically ~40 samples (3-5 captures
  most of the gain).
- *Necessary but Not Sufficient: Temperature Control and Reproducibility in
  LLM-as-Judge Safety Evaluations* — mitigations M1/M2 are temperature/seed;
  then: "(M3) Run epochs > 1 for the grader and report variance or confidence
  intervals... **the only mitigation that survives the deprecation of sampling
  parameters.** (M4) Surface grader disagreement as a first-class health metric;
  high-disagreement items are candidates for rubric revision or human
  adjudication."
- Vergo et al., *PoLL: Replacing Judges with Juries* (arXiv:2404.18796): a panel
  of diverse smaller judges beats a single GPT-4 judge, reduces intra-model
  bias, 7x cheaper; GPT-4 alone showed "high variance with minor changes to
  the prompt."
- Practice: real-world rubric judges implement exactly this (median-aggregates
  N judgments per criterion, reports spread, triggers near the decision gate).

(c) **Applicability.** Cheapest effective form: run Call 1 three times, take a
per-criterion majority vote on verdicts (requires verbatim requirement text for
matching — see §3), then score deterministically. If requirement sets don't
align, fall back to median of the three computed scores. Trigger **conditionally
on the borderline band** (e.g., single-run score within ±0.7 of the threshold
gets 2 extra runs) to bound cost — most JDs grade far from the boundary.
A bonus: per-criterion disagreement is diagnostic — a criterion that flips
DIRECT_HIT↔GAP across runs is a rubric-definition problem to fix, not just
noise to average away.

(d) **Cost.** 3x Call-1 calls when triggered; ~1.5-2x average with band
triggering. Median-of-3 is robust to a single outlier draw.

---

## 5. Output format constraints

(a) **What it is.** Schema-bounded output: JSON-only, categorical verdicts,
fixed field order.

(b) **Evidence.**
- OpenAI evals eval-templates.md: the eval prompt should "prime the model to
  answer in such a way that is easily parsable, e.g., multiple choice format or
  a simple yes/no"; `closedqa` even asks the model to repeat the verdict letter
  at the end for robust extraction.
- OpenAI/Anthropic structured-outputs docs: schema enforcement guarantees
  *format* compliance deterministically — but does not stabilize the *values*.
  Format constraints remove parse failures, not judgment variance.
- Stureborg et al.: bounded low-cardinality scales avoid the quantization
  artifacts of wide numeric ranges.

(c) **Applicability.** Already implemented (JSON-only stdout, fenced-block
tolerant parser in `llm_interface.py:40-70`). Two refinements: (i) fixed key
order in the JSON schema reduces token-path diversity; (ii) keep the score
*out* of the LLM entirely (§1) — a generated `grade` field is where
out-of-range output came from (ADR-0015).

(d) **Cost.** Free — already in place.

---

## 6. Parameter-level determinism levers when temperature can't be set

(a) **What it is.** What's actually controllable: seed, system_fingerprint,
temperature — none exposed by `devin -p` (verified in
`devin_cli.py`). The prompt-only substitutes:

(b) **Evidence.**
- OpenAI reproducible-outputs docs: determinism requires seed + identical
  params + unchanged system_fingerprint — and even then "mostly" identical.
  Implication: identical prompt bytes are the floor, not the ceiling.
- Multi-epoch grading + disagreement reporting + logging the effective grader
  config are the residual mitigations when sampling params are unavailable.
- Sclar et al., *Quantifying Sensitivity to Spurious Features in Prompt Design*
  (ICLR 2024, arXiv:2310.11324): meaning-preserving format changes alone swing
  accuracy up to 76 points — so the protocol files must stay byte-stable; every
  prompt edit is a grader "redeploy."

(c) **Applicability.** Already partially done: temp-file prompt delivery means
identical bytes; "commit to your first reasonable decision" anti-deliberation
rules; <500-token output budget. Additions: verbatim requirement quoting
(canonicalizes the cross-run join key), document-order listing, fixed JSON key
order, and logging the resolved model per call (audit table exists). Note the
pipeline inconsistency: `step4` uses `models.customizer` while `step7` uses
`models.grader` — a grader-model config exists and could host a *different*
model for a cheap two-model panel if the CLI supports alternates.

(d) **Cost.** Small prompt edits + logging; the panel variant costs 2x Call-1.

---

## 7. Known pitfalls (watch-list)

- **Position/selection bias** — Zheng et al., MT-Bench (NeurIPS 2023,
  arXiv:2306.05685): judges favor responses by presentation order (swap
  positions to test; consistency rate is the standard metric). Zheng et al.,
  ICLR 2024 (arXiv:2309.03882): bias is partly toward option-ID *tokens* —
  prefer named verdicts (DIRECT_HIT...) over lettered options; we already do.
  Keep rubric-table and requirement order fixed.
- **Verbosity bias** — MT-Bench + CALM (Ye et al., ICLR 2025,
  arXiv:2410.02736; 12 bias types). Long JDs/resumes can inflate verdicts;
  our "cite specific evidence" requirement is the right counter — keep it
  enforced (a verdict with no evidence citation should fail validation).
- **Self-preference/self-enhancement** — MT-Bench; Liu et al., *LLM Evaluators
  Recognize and Favor Their Own Generations* (NeurIPS 2024). **Applies
  directly**: `glm-5.2-high` writes the resume (step6) and grades it (step7).
  If the CLI supports a second model, set `models.grader` differently.
- **Anchoring across criteria** — Stureborg et al.: earlier verdicts bias later
  ones within a single generation. Per-criterion independent calls eliminate
  it but multiply cost; fixed order + majority-vote across runs is the cheaper
  approximation.
- **Leniency/severity asymmetry** — the JD protocol is deliberately "more
  lenient" (full background vs. single resume); fine as a designed offset, but
  it means JD and resume grades aren't on the same scale — don't tune one
  rubric off the other's distribution.
- **Grading drift** — Sclar et al. (format sensitivity); Shankar et al., *Who
  Validates the Validators* (UIST 2024, arXiv:2404.12272): "criteria drift" —
  the rubric co-evolves with observed outputs; treat `judgment_calls` as the
  drift sensor it was designed to be (ADR-0017) and promote recurring phrases
  into the fixed lists.
- **Arithmetic/compliance slips** — ADR-0015's 10.3; removed entirely by §1.
- **Threshold mismatch** — `jd_grade_threshold` default 7.0
  vs protocol's "≥ 8" (scoring protocol). Whichever is intended, the
  doc and code should agree; near-boundary scores are exactly where variance
  bites.

---

## Recommended shortlist (impact per effort)

1. **Score in Python, not via Call 2** — eliminates an entire failure class and
   one LLM call; the formula is already fully specified. (§1)
2. **Canonicalize extraction**: verbatim quotes, document order, replace
   "max 10 — pick most important" with a deterministic rule. Enables §4 and
   removes denominator noise. (§3)
3. **Median-of-3 (or per-criterion vote) on Call 1, triggered in the borderline
   band around the threshold** — the literature's only sanctioned fix when
   temperature/seed are unavailable. (§4)
4. **Anchor the verdict levels with exemplars + tighten the ADDRESSED/PARTIAL
   boundary** — pure prompt edit, shrinks the ambiguity zone that caused the
   documented 2.8-pt swing. (§2)
5. **Golden-set calibration + spread logging**: a handful of JDs with
   human-verdicted requirements; re-run on any prompt/model change and report
   per-criterion agreement, not just scores. (§7)
6. **Housekeeping**: point step4 at `models.grader`; reconcile the 7.0-vs-8
   threshold; consider a different grader model for step7 (self-preference).
