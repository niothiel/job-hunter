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

---

# Resume / LinkedIn bullet bank — CardCast

Source: repository code, documentation, benchmarks, and Git history across `mtg` (536 commits on
the current branch, April 2024 – September 2026; nearly the entire history authored by Val).
CardCast (cardcast.gg) is a production, browser-based platform for playing paper Magic: The
Gathering remotely. It combines real-time voice/video, synchronized multiplayer game state,
computer-vision card recognition, and a public content/marketing site across a FastAPI +
SQLAlchemy backend, React/TypeScript frontend, LiveKit/WebRTC media layer, ONNX Runtime inference,
Astro site, and Terraform-managed infrastructure.

Bullets are grouped by theme, written liberally like the Heeler bank above, and grounded in code,
commits, or checked-in benchmark results. Trim and tailor per role; remove commit hashes before
pasting into a resume.

**Role framing used for the resume:** Founder / Principal Software Engineer. The repository strongly
supports end-to-end founder-level ownership and near-solo authorship, but the title itself is an
inference; use Creator / Independent Software Engineer if that is more personally accurate.

---

## 0. Founder / 0-to-1 Product Ownership

- Founded and independently built **cardcast.gg**, taking it from a SIFT-based computer-vision
  experiment to a deployed, no-signup browser platform for remote tabletop gaming; owned product
  strategy, ML research, backend, frontend, infrastructure, operations, and launch across a
  536-commit current history. (`c94bad0`–`5abb3d6`)
- Designed and shipped the product end-to-end: live voice/video, authoritative multiplayer game
  state, interactive card recognition, virtual cards, gameplay tooling, and a public marketing and
  documentation site. (`beb98d7`, `22ada35`, `2e5969a`, `9172ea0`, `2f6f2b7`)
- Defined the product around an underserved use case—distributed Commander playgroups dissatisfied
  with existing card detection—and personally drove competitive research, positioning, roadmap,
  implementation, deployment, weekly dogfooding, and launch planning. (`docs/MARKETING.md`,
  `docs/COMPETITION.md`, `docs/LAUNCH_PLAN.md`, `3a1532d`)
- Progressed the core differentiator through multiple technical generations—classical SIFT
  matching, hybrid detection, learned DINO embeddings, vector retrieval, a standalone inference
  service, and reusable recognition/catalog libraries—while keeping a playable product shipping.
  (`b9c0652`, `d9aca68`, `91a2c5a`, `7cad8d2`, `73a1423`, `e9e2bae`)

## 1. Computer Vision, ML & Card Recognition

- Architected a reusable ONNX Runtime computer-vision pipeline that detects cards, batches DINO
  embeddings, resolves top-K matches, handles rotated cards, and reports stage-level latency;
  deployed the shared implementation across both the main application and standalone inference
  service. (`9e59372`, `73a1423`, `8986a07`, `5abb3d6`)
- Built a validated four-artifact recognition bundle spanning detector and embedder ONNX models,
  embedding matrices, and card metadata, with model-checksum and embedding-dimension validation to
  reject incompatible assets before inference. (`73a1423`, `1cd69a2`)
- Developed an end-to-end catalog pipeline that incrementally synchronizes Scryfall metadata and
  card images, performs multithreaded downloads with connection pooling, verifies SHA-256
  checksums, and generates deterministic batched embedding indexes. (`db83ed7`, `6a4eef2`,
  `f099a8d`, `1cd69a2`)
- Designed a reloadable in-memory card database that atomically publishes fully validated snapshots
  to concurrent readers, preventing partial or corrupt catalog updates while supporting normalized
  name, printing, and Oracle-ID lookup. (`76b54e6`)
- Re-architected recognition around standalone typed catalog and recognition libraries, migrating
  application consumers and tests, deleting 3,600+ lines of legacy detection/classification code,
  and removing OpenCV and FAISS from the application runtime. (`e9e2bae`, `8986a07`, `f7be60c`,
  `14a5458`, `59c32e0`, `22832f9`)
- Built a reproducible benchmark harness spanning backend single-/multi-threaded CPU, CUDA, browser
  single-/multi-threaded WASM, and WebGPU, with controlled warmups, raw per-run samples, hardware
  capture, and generated reports. (`26b9d0d`, `b19cce8`, `comprehensive_benchmark/`)
- Created and curated a real-world evaluation corpus of 178 labeled gameplay frames and 2,205 card
  crops, with video ingestion, machine-assisted labeling, human review, regression comparison, and
  detector/classifier dataset tooling. (`benchmark` submodule: `10b050c`, `4525a08`, `3cb8382`,
  `dc745ea`, `c4065f0`, `ab53b76`)
- Benchmarked DINO, ViT, EfficientNet, and patch-similarity approaches using top-1/top-5/top-10
  accuracy, mean rank, and latency, identifying a DINOv3 art-crop configuration with 55.2% top-1
  and 78.0% top-10 accuracy at 53.0 ms/image. (`benchmark` submodule: `310e2d5`)
- Extended the benchmark system to physical mobile devices over LAN/HTTPS with crash-safe partial
  result persistence; measured the production detector at 27.0 ms median on iPhone WebGPU versus
  166.6 ms on single-threaded WASM across 50 runs. (`b19cce8`,
  `comprehensive_benchmark/results/phone/REPORT.md`)
- Benchmarked alternative detector architectures and demonstrated that FP16 YOLO segmentation ran
  at 10.4 ms median wall time on an RTX 3070—4.2x faster than the reliable DINO baseline—while
  producing instance masks suitable for perspective correction. (`fc33863`,
  `docs/PERFORMANCE_PLAN.md`)
- Added recognition resilience for difficult inputs, including full-frame fallback, center-point
  filtering, 180-degree card orientation, messy backgrounds, large-card handling, and regressions
  for blank frames and false positives. (`1196ba2`, `f7be60c`)

## 2. Real-Time Media Platform (WebRTC / LiveKit)

- Architected and executed a staged migration from an N-squared peer-to-peer WebRTC mesh to a
  LiveKit SFU, extracting a transport-agnostic media interface, preserving backward-compatible mesh
  rooms, and making transport selection authoritative per game. (`d6e7143`, `58505dd`, `f623f96`,
  `cdc88a0`)
- Secured LiveKit access with 12-hour, room-scoped JWTs derived from signed session identity and
  verified against persisted game membership, preventing clients from minting credentials for
  arbitrary users or rooms. (`75ec570`, `0f99e7c`)
- Implemented adaptive streaming, dynacast, VP8 simulcast, reconnection backoff, and
  duplicate-identity handling while preserving raw 1920x1080 owner-camera frames for remote card
  recognition instead of processing dynamically downscaled subscriber video. (`f623f96`,
  `4a13359`)
- Added configurable LiveKit recording across participant, room-composite, and raw-track modes with
  S3-compatible storage, fail-open behavior, automatic room provisioning, and lifecycle cleanup.
  (`605a526`)
- Verified the SFU migration end to end across authentication, two-client media, adaptive-layer
  switching, 1080p owner capture, WebSocket-failure fallback, and legacy mesh coexistence;
  documented a three-player, 25-fps session carrying 5.68 GB upstream and 6.52 GB downstream.
  (`cdc88a0`, `docs/LIVEKIT.md`)

## 3. Multiplayer Backend & Real-Time State

- Designed and built the authoritative backend for an eight-player real-time game platform,
  persisting gameplay state in SQLAlchemy and synchronizing life totals, turns, timers, counters,
  commander damage/tax, eliminations, detections, and virtual cards over WebSockets. (`22ada35`,
  `966fbb2`, `2308e39`, `c582e4a`, `b584d02`, `704db9f`)
- Separated real-time connection management from domain logic into dedicated WebSocket and game
  manager layers with initial-state catch-up, persisted event history, owner election,
  duplicate-session replacement, targeted signaling, and room-wide broadcasts. (`22ada35`,
  `ced463f`, `704db9f`)
- Introduced discriminated Pydantic contracts for 24 inbound and 14 outbound WebSocket variants,
  published them through OpenAPI for generated TypeScript types, and rolled out monitor-only
  validation to detect contract drift without disrupting existing traffic. (`bee32a3`)
- Hardened long-lived connections with three-second server keepalives, ten-second liveness
  timeouts, client-side reconnect detection, safe connection replacement, and explicit close codes
  for superseded sessions and completed games. (`87e3333`, `297ddb7`)
- Implemented automatic game expiration and coordinated shutdown across database state, background
  threads, the asyncio loop, WebSocket broadcasts, and LiveKit rooms to prevent stale sessions and
  recording resources from remaining active indefinitely. (`d6d53f0`, `562f436`, `605a526`)

## 4. Frontend & Product Experience

- Built the real-time gameplay experience across React/TypeScript and FastAPI: life totals, turn
  order and timing, commander damage and tax, poison/experience/radiation counters, monarch and
  initiative status, player elimination, dice/coin rolls, and game-owner controls. (`22ada35`,
  `26ad3d9`, `84635c2`, `4c1aee6`, `6d60145`, `b584d02`)
- Developed an interactive card-recognition UX that maps detection geometry across cropped,
  scaled, and rotated video and supports region/whole-frame scans, hover previews, persistent
  pinning, grouped and de-duplicated history, card rulings, and selectable contour overlays.
  (`2e5969a`, `2aebcd6`, `024f835`, `bc8bbd1`, `9b8f3fb`, `c0f48b9`, `aeb4c65`)
- Designed and shipped synchronized virtual cards and tokens that players can drag, resize, rotate,
  pin, and delete over live video, persisting state through the game WebSocket and dynamically
  selecting image resolution from rendered size. (`9172ea0`, `704db9f`, `1a7c75e`, `044831d`)
- Improved long-running-game usability with persistent camera/microphone preferences, camera
  rotation, microphone disconnect versus mute, per-player volume controls, talking indicators,
  responsive table layouts, and resizable player strips/sidebars. (`61931d1`, `d05de8c`,
  `2e73f7f`, `f5e69a8`, `2c8d52f`, `3971cd7`)
- Modernized the frontend through React 19, Mantine 9, React Router 7, TypeScript, Vite, and ESLint
  upgrades, protected by mocked-backend Playwright interaction and pixel-diff regression tests for
  the media-heavy game screen. (`27a2870`, `90b40dc`, `95b3a45`, `7d4ac63`, `b1f3229`,
  `a6c814d`)
- Created shared design tokens consumed by both the Mantine application and Astro site, maintaining
  consistent color, typography, spacing, and semantic states across independent rendering stacks.
  (`4e7f3ce`, `79a1879`)
- Built developer tooling for rapid media and recognition iteration: an interactive detection lab,
  mocked WebSocket/API fixtures, fake-camera browser tests, deterministic multiplayer game seeds,
  prerecorded video feeds, and timeline scrubbing for reproducible demos. (`4eccac0`, `27a2870`,
  `1267a53`, `35612dd`)

## 5. Reliability, Security & Observability

- Protected API availability from compute-heavy vision workloads by first isolating inference in a
  managed subprocess pool and then extracting it into a standalone FastAPI service with readiness
  checks, typed responses, stage timings, timeouts, and CPU/GPU deployment paths. (`1173bed`,
  `dfebca1`, `5abb3d6`)
- Designed priority-aware inference backpressure with a CPU-sized bounded semaphore: best-effort
  auto-scans shed immediately with HTTP 429, while user-initiated scans wait up to five seconds
  before receiving a retryable 503. (`eba8fa9`)
- Built a reusable circuit breaker for remote inference with closed/open/half-open recovery,
  configurable thresholds, and correct control-flow exception handling; production opens after
  three failures and probes recovery after 15 seconds. (`dfebca1`)
- Implemented circuit breakers, outbound-request timeouts, reconnecting WebSockets, keepalives, and
  retry/backoff behavior across external deck services, real-time game state, and media transport.
  (`87e3333`, `875ca0d`, `297ddb7`, `03016e6`)
- Instrumented the stack with Sentry error reporting, browser tracing/session replay, backend log
  capture, domain-proxied PostHog analytics, structured access latency, per-stage inference timing,
  and Git-derived production version reporting. (`3830ce6`, `8024e7c`, `658ffcc`, `f850fdd`,
  `4e7f3ce`, `d4edfcd`)
- Hardened internet-facing traffic with trusted-host validation, HTTPS redirects, Cloudflare-only
  forwarded-header trust, malformed-header rejection, image-payload log redaction, and
  production-disabled development routes. (`03016e6`)
- Moved deployment secrets into ignored environment configuration and patched known `urllib3` and
  `minimatch` CVEs. (`ab21ce9`, `ffdc8f1`, `8d91116`)

## 6. Infrastructure, Deployment & Launch

- Provisioned and operated cardcast.gg on Oracle Cloud with Terraform, defining a 4-OCPU, 24-GB
  RAM, 200-GB ARM instance with automated recovery and compute monitoring; containerized the
  FastAPI, React, and Astro application for reproducible deployment. (`407bede`, `972f36d`,
  `c5aaaa9`)
- Built a multi-stage production image that compiles two TypeScript frontends, composes their static
  output, installs locked Python dependencies and shared libraries, embeds Git build metadata, and
  ships a single deployable FastAPI container. (`2f6f2b7`, `c5aaaa9`)
- Separated compute-intensive recognition into a standalone FastAPI inference service with a
  read-only model bundle, health endpoint, Docker packaging, NVIDIA GPU passthrough, restart policy,
  and a 20-GB memory limit. (`7cad8d2`, `df6843b`, `8f5ea68`, `5abb3d6`)
- Provisioned an AWS ML research environment in Terraform with 16 vCPUs, 128 GB RAM, and 300 GB
  storage, remote S3-backed state, and SSH restricted dynamically to the developer's current /32
  address. (`1ff41d7`)
- Built and launched an Astro marketing/content site alongside the React application, including
  responsive landing pages, documentation, release blog, sitemap/robots metadata, FAQ, competitor
  comparison, and a frictionless create-game path. (`2f6f2b7`, `3a1532d`, `a571b68`, `b97b211`)
- Created an SEO-targeted SpellTable-alternative page and product communication loop spanning
  release posts, Discord/community calls to action, in-product sharing/feedback paths, analytics,
  and a unified visual identity. (`b97b211`, `971b2ff`, `e336794`, `e1e21a0`, `8acde8a`,
  `79a1879`)

## 7. Engineering Practices

- Established a comprehensive quality gate spanning Ruff linting/formatting, mypy static analysis,
  Python unit tests, frontend ESLint and TypeScript compilation, and Playwright interaction and
  visual-regression testing. (`c864e26`, `7806e53`, `27a2870`, `d109a60`)
- Built Playwright visual-regression coverage around multiplayer games, settings, card history,
  onboarding, and overlays to support major React, Mantine, Vite, and router upgrades safely.
  (`27a2870`, `562f436`)
- Consolidated five Python projects under shared Ruff/mypy configuration, locked dependencies,
  typed package boundaries, and one `make check` workflow covering the web app, inference service,
  catalog, recognition library, and model tooling. (`c4a8223`, `187caef`)
- Used atomic streaming downloads (`fsync` + filesystem replacement), checksum validation, and
  immutable snapshot publication to ensure interrupted catalog/model updates cannot expose partial
  assets to production readers. (`76b54e6`)

---

## Recommended five-bullet resume cut

- Founded and independently built cardcast.gg, evolving a computer-vision prototype into a
  production browser platform for remote tabletop gaming with live voice/video, synchronized game
  state, card recognition, and virtual cards.
- Migrated real-time media from an N-squared WebRTC mesh to a LiveKit SFU with room-scoped JWTs,
  adaptive streaming, simulcast/dynacast, reconnect handling, recording, and preserved 1080p source
  frames for recognition.
- Architected a reusable ONNX Runtime recognition platform spanning card detection, batched DINO
  embeddings, top-K retrieval, validated model bundles, incremental Scryfall synchronization, and
  atomic hot-reloaded catalogs.
- Built a six-platform CPU/CUDA/WASM/WebGPU benchmark suite and 178-frame/2,205-crop evaluation
  corpus; measured 27.0 ms median detector inference on iPhone WebGPU and identified a 10.4 ms GPU
  segmentation architecture.
- Delivered and operated the full product stack across FastAPI, SQLAlchemy, React/TypeScript,
  WebSockets, Astro, Docker, Terraform, Oracle Cloud, Sentry, PostHog, and Playwright.

## Notes / scope caveats

- “Founder / Principal Software Engineer” is inferred from near-exclusive authorship and complete
  product ownership; change the title if that does not match how you represent the project.
- The repository supports production deployment, active dogfooding, and a public launch, but does
  not provide defensible active-user, revenue, availability, conversion, or traffic metrics.
- Keep security claims specific to the implemented controls above; do not generalize them into a
  broad security/compliance claim.
- Auto-scan and backpressure exist, but the current frontend deliberately keeps user auto-scan
  disabled. Avoid claiming that always-on detection is currently shipped.
- The current WebSocket registry is process-local and persistence uses SQLite; avoid describing the
  backend as distributed or horizontally scalable.
