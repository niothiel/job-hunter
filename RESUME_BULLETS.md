# Resume / LinkedIn bullet bank — Heeler App

Source: `git log --author="vkomarov@heeler.com"` across `heeler-app` (~1700 commits, Oct 2024 – Sep 2026).
Heeler is an AppSec/SCA platform: vulnerability findings, remediation, SBOM/dependency analysis,
AI-agent-driven auto-fix, workflow automation, and tenant/service administration, on a FastAPI +
Postgres backend with a React/TS (Mantine) frontend.

Bullets are grouped by theme, written liberally (per your instructions) but grounded in actual
commits — trim/tailor per job posting. Commit hashes are short refs into this repo for your own
verification; drop them before pasting into an actual resume.

**Already confirmed by you:** Fix Now agentic remediation feature = your design. Backend-execution
move for the workflow engine = your design decision. Shared FE component library = team-shared,
you contributed heavily (not sole owner). Mypy strict-typing rollout = you personally drove it.
Transparent package-manager proxy (agent-side integration) = your design. DeepEval evaluation-suite
overhaul = solo effort. `package_ecosystem` migration = you owned it end-to-end. Agent "Memories"
system (including admin UI) = your design. Verbs below reflect all of these answers — no open
role questions remain except #5 below.

---

## 0. Autonomous LLM Remediation Agent (LangGraph / AWS Bedrock)

*A sandboxed, LangGraph-based autonomous agent that investigates and remediates SAST/SCA
vulnerabilities and answers CI questions — the "Fix Now" feature's engine.*

- Designed and built the core LangGraph agent execution engine for autonomous vulnerability
  remediation: unified previously-separate CI and remediation code paths into a single
  graph-building/execution pipeline, decoupled from direct database access for testability.
  (`55ade2324e`, `0697435469`)
- Re-architected the Redis-backed LangGraph checkpointer from full replay-history storage to
  shallow (latest-only) checkpoints, cutting memory/storage overhead and eliminating stale-write
  buildup; redesigned the Redis keyspace with ACL-scoped permissions, TTLs, and a reaper process
  for cleanup. (`88eb5e36c8`, `8d0314baa3`, `9240e36ced`)
- Designed the agent-side integration of a transparent network proxy for package managers (npm,
  pnpm, uv, Go modules, Yarn Berry, pip, Poetry), letting the sandboxed agent safely resolve and
  upgrade dependencies without corrupting lockfile URLs, including a proxy-receipt client to
  disambiguate first- vs. third-party packages after an upgrade. (`c543640831`, `7765cd6f04`,
  `dcc85bb913`, `d003d8ba90`)
- Designed and shipped an agent long-term memory system: sandboxed `get/store/update/delete_memory`
  tools scoped to a repository's working set, with operations journaled and applied post-run and
  injected into SCA/SAST prompt context, plus the administration UI for inspecting and managing
  stored memories. (`2af7ce1352`, `e795856f6b`, `9173392de3`)
- Migrated the agent's LLM calls to AWS Bedrock with a feature-tagged inference profile for
  per-team cost attribution, hardened against Bedrock throttling with full-jitter exponential
  backoff, and drove successive model upgrades (Claude 3.7 → 4.0 → newer) for the remediation
  agent. (`ed57964d2f`, `1e42c90bff`, `ca47fb8df7`)
- Consolidated a sprawling set of ad hoc system prompts (CI, remote remediation, sandbox,
  guardrail, local chat) into a single, consistently-named prompt module. (`1b77450e62`,
  `664665d73f`)
- Hardened LLM-generated patch application: fixed encoding mismatches that corrupted diffs, capped
  max diff size from the sandboxed container, and excluded build artifacts from generated
  remediation patches. (`e2955167cd`, `ac0b1a3c1b`, `99d0f4e709`)
- Implemented a custom LangGraph `@tool` decorator to correctly serialize Pydantic models, and
  built agent tools for paging through large files by line count and viewing grep match context.
  (`4a1e32260e`, `86460ed325`, `9d619f2a26`)
- Built cross-toolchain build-environment detection for the agent sandbox: auto-detecting and
  pinning `uv`/Poetry tool versions to match project lockfiles by walking up the workspace tree.
  (`74bfd80955`, `947ee50541`)
- Solely designed and overhauled the DeepEval-based evaluation suite used to gate prompt/model
  changes for the remediation agent before release. (`21f2be582c`)

## 1. AI Agentic Remediation ("Fix Now") — product surface

- Designed and shipped an end-to-end AI-agent-driven auto-remediation feature ("Fix Now"): backend
  execution orchestration with concurrency limits, private-registry health gating, and graceful
  error handling; conversation/tool-call persistence for auditability; and a frontend
  execution-tracking UI with live status, a chat/tool-use drawer exposing the agent's reasoning,
  and feature-flagged rollout by language and complexity. (`e5cfb58173`, `b9393c5669`,
  `9c4c32a4ca`, `3a84721e97`, `01534ec3ec`, `db15a7dd3b`, `e8ea259087`)
- Built an agent execution triage system, including LLM-generated developer-facing summaries for
  failed executions and multi-dimensional filtering (agent type, initiator, source, PR status).
  (`18901f9781`, `e704f6861f`, `ad36f482ec`)
- Designed a scoped, JWT-based API token system (replacing an earlier ApiToken approach) to power
  secure, shareable remediation links, with FastAPI-dependency-based verification. (`cbc010e026`,
  `8fcab1467c`, `c5d184e648`, `ccda390c77`)

## 2. Software Composition Analysis, SBOM & Vulnerability Remediation Engine

- Designed and built a multi-ecosystem version-parsing and comparison engine from scratch (npm,
  PyPI, Maven, Go, NuGet, Ruby, plus a generic fallback), including ecosystem-specific semantics
  like Ruby's pessimistic `~>` constraint operator and date-based version ordering. (`48d6ae863f`,
  `4651c01065`, `93061bef2c`, `ea5880299d`)
- Built the vulnerability "solution calculator" that determines minimum-safe-upgrade paths across
  patched/unaffected version ranges, including transitive (parent-package) solutions and a
  "successor package" concept for deprecated/renamed dependencies, with the engine hardened to
  always surface an actionable answer rather than fail silently. (`a7aaef6cea`, `2a14a8a052`,
  `32818910bc`, `b706bbdf10`, `0c6964a414`)
- Built dependency-graph construction from CycloneDX SBOMs to derive direct/indirect dependency
  relationships and transitive paths to vulnerable packages, replacing heuristic classification
  with real graph traversal. (`1eb765572a`, `e3e0f953f9`, `34740952bf`)
- Implemented SBOM generation and export (CycloneDX-based, internally-typed Pydantic models) for
  code roots, services, deployments, and apps, including a downloadable `sca.json` for admins.
  (`fa5e291f86`, `a74ff47cac`, `d931f06ddc`, `2b80e61d03`)
- Shipped an LLM-generated "Upgrade Guidance" feature producing human-readable remediation advice,
  including prompt tuning for accuracy and flagging guidance that falls outside the model's
  knowledge cutoff. (`9d727451ab`, `dd34a1f14a`, `23ae822d5a`)
- Built the global remediations feature end-to-end (API route, query layer, cross-repository UI
  with ecosystem/manager filtering) and the Code Root Finding Details / vulnerability overview
  page with EPSS/CVSS-based SLO tracking. (`36777cddad`, `b6bc1d8f15`, `e638acd040`, `bb15c3f3dc`)
- Solely owned and drove a large, multi-week, cross-cutting rename and data-model migration from a
  language-based `package_manager` model to an ecosystem-based `package_ecosystem` model across the
  database schema, background jobs, remediation/solution engine, workflow engine, guardrail
  detection, and frontend — dozens of files, without breaking existing tenant data. (`567c137ecb`,
  `194bed8194`, `ac3849a5d1`, `5f3f97d79c`, `125fc7897a`)
- Reworked internal dependency-identity resolution to key off package ecosystem rather than
  programming language, fixing misattribution for cross-runtime ecosystems (e.g. npm packages
  usable from both TypeScript and JavaScript). (`e0abba233a`)
- Built a correlation pipeline mapping first-party package coordinates back to their source code
  root and changeset, with persistent reconciliation and chunked PostgreSQL upserts that remain
  below psycopg 3's 65,535-parameter limit. (`8485cd4a6d`, `43c0a5d289`)

## 3. Workflow Automation Engine & Third-Party Integrations

- Designed and led the move of tenant workflow automation execution from synchronous frontend/
  request-thread execution to an asynchronous, worker-queued backend execution engine (three
  execution modes), adding rate limiting to stop runaway trigger storms, automatic disable +
  tenant-admin notification on abuse, and a success-rate counter for observability. (`0296bcb4c5`,
  `7958678085`, `25fe7e3987`, `c96788d8a2`)
- Built out the workflow trigger/action catalog: actions to open Jira remediation tickets, fire
  webhooks, trigger SBOM generation on new deployments, and post Slack notifications; triggers for
  newly-discovered compromised (malicious) dependencies, service environment/tier filters, and an
  "Accept All" filter type. (`db02cf0642`, `620d3556a5`, `d690279895`, `75e549408e`, `7ba4ed135f`,
  `ffaf26bbf5`, `d53518c2ca`)
- Led a refactor of the workflow conditional/filter engine, replacing an ad-hoc condition system
  with a structured filter architecture supporting package version, module/repository, findings,
  and service environment/tier conditions. (`75b4469221`, `95845429c5`, `b42a8a0ba6`, `ffaf26bbf5`)
- Owned the Jira ticketing integration for remediation workflows: ticket creation UI, linking and
  unlinking issues to remediations, automatic closure on remediation completion, and richer ticket
  content. (`e57cad3abd`, `805e238cc0`, `0a97e6327b`, `cb8bc5c282`)
- Integrated third-party threat-intelligence feeds into malicious-package/guardrail detection,
  including migrating the Known Exploited Vulnerabilities (KEV) data source from CISA to VulnCheck.
  (`b8bc0bb92a`, `58b5a439f2`)

## 4. Backend Platform Reliability, Scale & Performance

- Diagnosed and fixed a production database deadlock in the job scheduler caused by two concurrent
  processor instances lock-ordering `scheduler_tracking` rows in opposite directions, re-deriving
  and re-applying a session-isolation fix that had previously been reverted for the wrong reason.
  (`6dcba565ce`)
- Fixed a scheduler double-enqueue bug for long-running jobs by tracking last-run job identity to
  correctly compute next-run timing (ENG-4873), and fixed a connection-recycling bug where an
  invalidated DB handle could silently disable a processor while still holding scheduler locks.
  (`80379a0df3`, `c8ae696a76`)
- Implemented graceful shutdown across all worker types (API, job workers, scheduler) to stop jobs
  being force-killed by the container platform's shutdown timeout, centralizing shutdown/health-check
  logic application-wide. (`ae2ca62086`)
- Designed and shipped a caching layer (materialized summary table + scheduled refresh job) to make
  the global dependency listing usable for very large tenants where the live query had become
  untenable, plus a rewritten dependency-statistics query (single-pass conditional aggregation vs.
  4-CTE) with a covering index and tuned autovacuum settings. (`7862b5dbdd`, `a6010c2278`)
- Improved production query performance broadly: fixed multiple N+1 queries (services,
  vulnerability exploitation, search), added a composite index and rewrote a full-table-scan terms
  endpoint, and cut frontend graph/path call latency. (`320afc86bf`, `1f9189f6a7`, `69b2509422`,
  `ed4371ab85`)
- Added scoped, expiring API tokens (closing a gap where tokens had no expiration enforcement),
  per-job SQL statement tagging to trace slow queries back to their originating background job, and
  per-job Sentry isolation scopes to stop cross-job error-report data bleed. (`cbc010e026`,
  `c4a23b3b2e`, `16e054d153`)
- Extended the background job framework to accept multiple typed, Pydantic-validated keyword
  arguments at enqueue time (previously limited to a single kwarg, fixing a race where multiple
  parents enqueuing the same child job with different arguments could silently drop an enqueue),
  and drove engineering hygiene by requiring/backfilling docstrings across the entire job catalog
  for UI discoverability. (`32855f3874`, `6144778288`, `9eeb8a42a0`, `8afd85513e`)
- Added multi-tenant backfill support, one-off vs. recurring job semantics, and a "cause" field on
  scheduler tracking records to make it debuggable why any given job ran. (`257e50fb5c`,
  `ae07834425`)
- Reworked compute-heavy remediation scheduling around incremental change signals and deterministic
  time bucketing, eliminating thundering-herd scheduling while recording trigger reasons and
  improving package-resolution cache utilization. (`870724e494`, `7963b4ddd9`, `888f5975a1`,
  `73df7e0d52`)
- Built out harvest/connector jobs across AWS, Azure, and GCP (via CloudQuery) plus user-resolver
  jobs for GitHub, GitLab, and Jira to reconcile external identities against internal users.
  (`24a93cfd47`, `e9504fc59a`, `1ed90e948e`)

## 5. Frontend Engineering

- Designed and built the core vulnerability remediation UI end-to-end — from an initial prototype
  overview page through a unified Service + Code Root remediation experience with centralized API
  endpoints and CVSS/EPSS-based SLO tracking. (`0b22e39a1`, `d01964f311`, `a5b7d70e39`)
- Implemented Software Composition Analysis UI: global dependency tree/graph visualization,
  module-level filtering, and SBOM export/download for code roots. (`fa5e291f86`, `b42a8a0ba6`,
  `320afc86bf`, `d931f06ddc`)
- Built service ownership/tech-lead management features and super-admin tooling: tenant
  feature-flag administration and external user management UIs. (`ae8c84ea1c`, `caaba49dce`,
  `fee40f5f9e`, `a2ca10ed14`)
- Built an ownership-recommendation pipeline that correlated external identity data with services,
  surfaced suggested owners, and automatically assigned remediation work through tenant-configurable
  policies. (`38c78dab54`, `cf1a4abe49`, `35373758bb`, `565d9cc0bf`)
- Implemented SLO configuration UI (per-severity strategy settings, status badges surfaced across
  finding/vulnerability views) and Operational Health monitoring views for connection status and
  error/summary reporting. (`bb15c3f3dc`, `28090ae72d`, `3f7fa984e5`, `80f24d7a23`)
- Contributed extensively to the team-shared component library (data tables/grids, modals,
  drawers, filter components, custom renderers) consumed across the entire product's feature set.
  (`src/ts/ui/src/components`, `src/ts/ui/src/common`)
- Built dependency vulnerability path/reachability visualization (surfacing which code paths
  actually reach a vulnerable function) and a Global Dependencies view with cross-repo filtering,
  backed by a caching layer for performance. (`000b7ad94d`, `320afc86bf`, `7862b5dbdd`)
- Maintained the typed API client layer generated from the backend's OpenAPI schema, including the
  schema-regeneration workflow and typing improvements for filter objects. (`c770351ccd`,
  `c125b14fc0`, `90300a41b9`)

## 6. Engineering Practices

- Drove a long-running, multi-month initiative to bring the entire Python backend under
  `mypy --strict`, switching the project from opt-in to opt-out type checking, annotating
  previously-untyped code across workers, the database layer, API routers, workflow, and
  remediation modules, and enforcing it in CI. (`dba4310fe1`, `5552048cd9`, `6b4b7cc758`,
  `d4b9d9992d`, `3e9917eb19`, `bab1a5275e`, `96b45bce91`)
- Migrated the SQLAlchemy ORM layer from legacy `Query`-style calls to SQLAlchemy 2.0's `select()`
  construct and declarative-base 2.0 syntax across the codebase. (`10fe3fd054`, `6a8ad6ea64`)
- Revamped how Alembic migrations run in production, replacing a subprocess-based invocation with
  direct in-process calls, and fixed a bug where a "migrate one tenant" admin action was
  incorrectly migrating every tenant. (`8ba0834892`)

---

## Notes / scope caveats
- Alembic's core dual-branch (tenant/common) migration tooling, the callgraph explorer, policy
  engine, scorecards, taxonomy, and end-of-life modules show little-to-no history from you — not
  included; these appear to be other engineers' work.
- Not yet investigated in depth (smaller footprint, but flag if resume-relevant): container image
  scanning, individual cloud/CI integrations beyond what's listed, guardrail policy engine details,
  threat modeling. Say the word if you want a deeper pass on any of these.

## Open questions for you
1. Anything else you're proud of that isn't reflected here (a specific integration, a customer
   escalation you resolved, an outage you led the response on) that I should add?
