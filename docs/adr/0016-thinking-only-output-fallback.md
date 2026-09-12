# Thinking-only output fallback for LLM calls

**Status:** accepted

When the model puts valid JSON in its `thinking` field with empty
`content`, the pipeline extracts the JSON from the thinking field as a
fallback. A warning is logged. All grading and truthfulness prompts were
updated to explicitly instruct the model to output JSON as content, not
thinking.

## Context

Overnight real-LLM evaluation on 2026-09-11 observed a resume grading
scoring call (trust-in-soda retry 3) where the model produced a complete,
valid grade JSON (3152 chars) entirely in the `thinking` field. The
`content` field was empty. The `devin -p` CLI outputs only `content` to
stdout, so the pipeline saw empty output and treated it as a failure.

This is a model deviation from the protocol spec, which says "Output ONLY
valid JSON to stdout." The model correctly computed the grade but placed
it in the wrong channel.

## Considered Options

- **Always require content, retry on empty:** Strengthen the prompt to say
  "output as content, not thinking" and retry on empty output. Rejected
  because it wastes a successful model call — the model did the work, it
  just put the answer in the wrong field. Retrying may produce the same
  result if the model consistently deviates on certain prompts.

- **Extract from thinking only:** Parse the export file for thinking
  content when stdout is empty. No prompt change. Rejected as incomplete —
  the root cause is the model deviating from the spec. Without a prompt
  fix, the fallback would be used on every call for affected prompts,
  adding latency (export file parsing) to the hot path.

- **Extract + warn + fix prompt (chosen):** All three: (1) parse the
  export file for thinking content when stdout is empty, (2) log a warning
  when the fallback is used, (3) update all protocols to explicitly say
  "CRITICAL: Output your JSON as content, not as thinking. The orchestrator
  reads only your content output — anything in your thinking field is
  invisible to it."

## Consequences

- **`devin_cli.py` always passes `--export` to a temp file.** This adds
  a small I/O cost per call but ensures the export file is available for
  the thinking fallback. If the caller provides their own `export_path`,
  that is used instead.

- **The fallback parses the export file format.** The export file is a
  JSON conversation transcript. `_extract_thinking_from_export` handles
  two known formats: `{"messages": [...]}` and `{"nodes": [{"chat_message":
  "..."}]}`. If the export format changes, the parser must be updated.

- **The warning is logged via `structlog`.** The warning includes the
  attempt number and thinking length, so recurring thinking-only outputs
  are visible in logs.

- **All 6 protocol files updated.** The reasoning and scoring protocols
  for both grading and truthfulness now include the content-vs-thinking
  instruction. This is a prompt-level mitigation — the fallback is the
  safety net.

- **The fallback only triggers when stdout is empty.** If stdout has
  content (even partial), the fallback is not used. This prevents the
  fallback from masking other output issues.
