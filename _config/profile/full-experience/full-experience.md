# Val Komarov — Full Career History

> Sourcing reference transcribed from the Google Docs super-résumé, “VAL KOMAROV RESUME 2026-09-15,” and expanded with the repository-grounded Heeler and CardCast material in `RESUME_BULLETS.md`. Every customized résumé claim must be traceable to this document or the base résumé. Never use this document as the customization base directly.

## Contact
Fairfax, VA | (571) 338-2310 | niothiel@gmail.com | linkedin.com/in/val-komarov

## Summary
Distinguished software engineer with 12+ years building security, AI, cloud, real-time, and data-intensive products. Deep experience in Python, Go, AWS, PostgreSQL, React, and TypeScript, with a track record of taking products from concept to production, improving reliability at scale, and leading high-impact technical initiatives.

## Technology
Python, Go, FastAPI, PostgreSQL, MySQL, SQLAlchemy, Redis, Pydantic, AWS, AWS Bedrock, ECS Fargate, RDS, React, TypeScript, JavaScript, WebSockets, WebRTC, LiveKit, ONNX Runtime, Docker, Terraform, Oracle Cloud, Linux (Ubuntu, Debian, Alpine), Bash, Git, Jenkins, GitHub Actions, GraphQL, Playwright, Sentry, PostHog

## Experience

### Heeler — Distinguished Software Engineer (October 2024 – Present) · Remote
Full-stack product engineer building an AppSec/SCA platform spanning autonomous AI remediation, vulnerability analysis, workflow automation, and multi-tenant platform infrastructure.

#### Autonomous AI Remediation

* Designed and built the core LangGraph execution engine for autonomous SAST/SCA remediation, unifying CI and remediation paths into one testable pipeline decoupled from direct database access
* Designed and shipped the end-to-end Fix Now product using AWS Bedrock, with private-registry health gating, concurrency controls, graceful failure handling, auditable conversation and tool-call persistence, and live execution tracking
* Re-architected Redis-backed LangGraph checkpointing from full replay history to shallow latest-only checkpoints, with ACL-scoped keys, expiration, and automated cleanup
* Designed the agent-side integration for a transparent package-manager proxy supporting npm, pnpm, Yarn Berry, pip, Poetry, uv, and Go modules without corrupting lockfile URLs
* Designed a repository-scoped long-term memory system with sandboxed read/write tools, post-run journaling, prompt injection, and an administration UI
* Migrated agent inference to AWS Bedrock with feature-tagged cost attribution and full-jitter exponential backoff for throttling
* Hardened generated patch application against encoding mismatches, excessive diff size, and build-artifact inclusion
* Built agent tools for Pydantic-safe LangGraph serialization, paged file reading, contextual search results, and project-specific package-tool detection
* Solely designed and overhauled the DeepEval evaluation suite used to gate remediation prompt and model changes before release
* Built execution triage with LLM-generated developer summaries for failures and filtering by agent type, initiator, source, and pull-request status
* Designed scoped, expiring JWT-based API tokens for secure, shareable remediation links

#### Software Composition Analysis and Vulnerability Remediation

* Designed and built a version-parsing and comparison engine for npm, PyPI, Maven, Go, NuGet, Ruby, and generic ecosystems, including ecosystem-specific constraint and ordering semantics
* Built the solution calculator for minimum-safe upgrades across patched and unaffected ranges, transitive parent-package solutions, and successor packages
* Built CycloneDX dependency-graph traversal to derive direct and indirect dependency relationships and paths to vulnerable packages
* Implemented typed CycloneDX SBOM generation and export for code roots, services, deployments, and applications
* Shipped LLM-generated upgrade guidance with accuracy-focused prompt tuning and model-knowledge-cutoff warnings
* Built global remediations end-to-end across APIs, queries, and a cross-repository UI with ecosystem and package-manager filtering
* Owned the cross-cutting migration from `package_manager` to `package_ecosystem` across database schema, background jobs, remediation, workflows, guardrails, and frontend without breaking existing tenant data
* Built first-party package correlation back to source code roots and changesets using persistent reconciliation and chunked PostgreSQL upserts

#### Workflow Automation and Integrations

* Designed and led the move from synchronous request-thread workflow execution to an asynchronous worker-queued engine with three execution modes, rate limiting, automatic abuse shutdown, tenant-admin notification, and success-rate observability
* Built workflow triggers and actions for Jira tickets, webhooks, Slack notifications, SBOM generation, compromised dependencies, and service environment and tier filters
* Led replacement of the ad hoc workflow condition system with structured filters spanning package versions, modules, repositories, findings, and services
* Owned the Jira remediation integration across ticket creation, issue linking and unlinking, automatic closure, and richer ticket content
* Integrated third-party threat intelligence for malicious-package and guardrail detection, including migration of Known Exploited Vulnerabilities data from CISA to VulnCheck

#### Platform Reliability, Scale, and Product Engineering

* Diagnosed and fixed a production scheduler deadlock caused by concurrent processors locking tracking rows in opposite order
* Fixed long-running-job double enqueue and invalid database connection recycling that could silently disable a processor while retaining locks
* Implemented coordinated graceful shutdown and centralized health checks across API, job-worker, and scheduler processes
* Designed a materialized-summary cache and scheduled refresh for global dependencies at large-tenant scale; rewrote dependency statistics into a single-pass query with a covering index and tuned autovacuum
* Removed N+1 queries across services, vulnerability exploitation, and search; rewrote a full-table-scan terms endpoint and reduced dependency-graph latency
* Added SQL statement tagging and isolated Sentry scopes per background job to improve production diagnosis and prevent cross-job context leakage
* Extended the job framework with multiple typed, Pydantic-validated keyword arguments, multi-tenant backfills, one-off and recurring semantics, and explicit run causes
* Reworked compute-heavy remediation scheduling around incremental signals and deterministic time buckets to prevent thundering herds and improve cache utilization
* Built cloud harvest jobs for AWS, Azure, and GCP and identity resolvers for GitHub, GitLab, and Jira
* Designed and built vulnerability-remediation, dependency-graph, reachability, SBOM, SLO, operational-health, ownership, and administration experiences across FastAPI, React, and TypeScript
* Built an ownership-recommendation pipeline that correlated external identities with services and assigned remediation work through tenant-configurable policies
* Contributed extensively to the team-shared React component library and maintained the typed OpenAPI-generated frontend client
* Drove a multi-month initiative to bring the Python backend under `mypy --strict`, switching from opt-in to opt-out checking and enforcing it in CI across workers, database, APIs, workflows, and remediation
* Migrated the SQLAlchemy ORM layer from legacy Query APIs to SQLAlchemy 2.0 `select()` and declarative syntax
* Replaced subprocess-based Alembic execution with in-process calls and fixed a tenant-scoping defect in the migration administration workflow

Technologies: Python, Go, FastAPI, PostgreSQL, SQLAlchemy, Pydantic, Redis, AWS Bedrock, LangGraph, DeepEval, React, TypeScript, Mantine, Docker, AWS ECS Fargate, AWS RDS, Terraform

### CardCast (cardcast.gg) — Founder / Principal Software Engineer (August 2025 – Present) · Remote
Ongoing independent project, concurrent with full-time employment, building a browser platform for remote tabletop gaming with real-time media, synchronized gameplay, and computer-vision card recognition.

#### Founder and Product Ownership

* Founded and independently built cardcast.gg from a SIFT-based computer-vision experiment into a deployed, no-signup browser platform; owned product strategy, ML research, backend, frontend, infrastructure, operations, and launch
* Shipped live voice and video, authoritative multiplayer state, interactive card recognition, virtual cards, gameplay tooling, and a public marketing and documentation site
* Progressed the core recognition system through SIFT matching, hybrid detection, DINO embeddings, vector retrieval, a standalone inference service, and reusable recognition and catalog libraries while keeping the product playable

#### Computer Vision and Machine Learning

* Architected a reusable ONNX Runtime pipeline for card detection, batched DINO embeddings, top-K matching, rotated-card handling, and stage-level latency reporting across the application and standalone inference service
* Built a validated recognition bundle spanning detector and embedder models, embedding matrices, and card metadata, rejecting incompatible assets using checksums and embedding dimensions
* Developed an incremental Scryfall catalog pipeline with multithreaded image downloads, connection pooling, SHA-256 verification, and deterministic batched embedding generation
* Designed a reloadable in-memory card database that atomically publishes validated snapshots to concurrent readers
* Re-architected recognition around standalone typed libraries, removing more than 3,600 lines of legacy detection code and eliminating OpenCV and FAISS from the application runtime
* Built a reproducible benchmark suite spanning single- and multi-threaded CPU, CUDA, single- and multi-threaded WASM, and WebGPU with controlled warmups, raw samples, and hardware capture
* Created a real-world evaluation corpus of 178 labeled gameplay frames and 2,205 card crops with video ingestion, machine-assisted labeling, human review, and regression tooling
* Benchmarked DINO, ViT, EfficientNet, and patch-similarity approaches; identified a DINOv3 configuration with 55.2% top-1 and 78.0% top-10 accuracy at 53.0 ms per image
* Extended benchmarking to physical mobile devices and measured the production detector at 27.0 ms median on iPhone WebGPU versus 166.6 ms on single-threaded WASM across 50 runs
* Evaluated FP16 YOLO segmentation at 10.4 ms median on an RTX 3070, 4.2 times faster than the reliable DINO baseline while producing masks suitable for perspective correction

#### Real-Time Media and Multiplayer Systems

* Architected a staged migration from an N-squared peer-to-peer WebRTC mesh to LiveKit SFU by extracting a transport-agnostic media layer and preserving backward-compatible rooms
* Secured LiveKit with 12-hour room-scoped JWTs derived from signed identities and verified against persisted game membership
* Implemented adaptive streaming, dynacast, VP8 simulcast, reconnection backoff, duplicate-identity handling, and preservation of raw 1920x1080 source frames for recognition
* Added configurable participant, room-composite, and raw-track recording with S3-compatible storage, fail-open behavior, automatic provisioning, and cleanup
* Designed and built the authoritative eight-player backend, persisting and synchronizing gameplay state over WebSockets
* Separated connection management from game-domain logic with initial-state catch-up, event history, owner election, duplicate-session replacement, targeted signaling, and room broadcasts
* Defined discriminated Pydantic contracts for 24 inbound and 14 outbound WebSocket variants, published them through OpenAPI for generated TypeScript types, and introduced monitor-only validation for contract drift
* Hardened long-lived connections with server keepalives, liveness timeouts, reconnect detection, safe connection replacement, and explicit close codes
* Implemented automatic game expiration and coordinated cleanup across database state, background threads, asyncio, WebSocket clients, LiveKit rooms, and recording resources

#### Frontend, Reliability, and Operations

* Built the React and TypeScript gameplay experience for life totals, turns, timers, counters, commander mechanics, player elimination, dice and coin rolls, and owner controls
* Developed an interactive recognition UI that maps geometry across cropped, scaled, and rotated video and supports region scans, previews, pinning, history, rulings, and contour overlays
* Designed synchronized virtual cards and tokens that players can drag, resize, rotate, pin, and delete over live video with state persisted through WebSockets
* Modernized the frontend through React 19, Mantine 9, React Router 7, TypeScript, Vite, and ESLint upgrades, protected by Playwright interaction and pixel-diff tests
* Built rapid-iteration tooling with an interactive detection lab, mocked WebSocket and API fixtures, fake-camera tests, deterministic game seeds, prerecorded feeds, and timeline scrubbing
* Protected API availability by extracting compute-heavy vision inference into a standalone FastAPI service with readiness checks, typed responses, timeouts, and CPU/GPU deployment paths
* Designed priority-aware inference backpressure that sheds background scans immediately and gives user-initiated scans a bounded wait
* Built a reusable remote-inference circuit breaker with closed, open, and half-open recovery and configurable thresholds
* Instrumented the stack with Sentry, browser tracing and replay, backend log capture, PostHog, structured request latency, per-stage inference timing, and Git-derived release versions
* Hardened public traffic with trusted-host validation, HTTPS redirects, restricted forwarded-header trust, malformed-header rejection, payload redaction, and production-disabled development routes
* Provisioned and operated cardcast.gg on Oracle Cloud with Terraform and containerized FastAPI, React, and Astro for reproducible deployment
* Built a multi-stage production image that compiles two TypeScript frontends, installs locked Python dependencies and shared libraries, embeds Git metadata, and serves the product from one deployable container
* Provisioned an AWS ML research environment through Terraform with remote state and source-address-restricted SSH access
* Built and launched an Astro marketing and documentation site with responsive landing pages, release notes, search metadata, comparison content, and a frictionless game-creation path
* Established a quality gate spanning Ruff, mypy, Python tests, ESLint, TypeScript compilation, and Playwright interaction and visual-regression tests across five Python projects and two frontends

Technologies: Python, FastAPI, SQLAlchemy, SQLite, Pydantic, React, TypeScript, Mantine, Astro, WebSockets, WebRTC, LiveKit, ONNX Runtime, Docker, Terraform, Oracle Cloud, AWS, Playwright, Sentry, PostHog

Sourcing limits: The WebSocket registry is process-local and persistence uses SQLite. Benchmark results describe measured configurations, not production-wide latency or recognition accuracy. Background-scan backpressure is implemented, but the current frontend keeps automatic scanning disabled. No defensible user-count, revenue, traffic, or availability metrics are provided by the source.

### DivvyCloud (Acquired by Rapid7) — Consulting Software Engineer (December 2019 – October 2024) · Arlington, VA
Multi-disciplinary engineer proficient in problem-solving and adept at wearing multiple hats. Committed to resolving persistent challenges within the product.

* Spearheaded the implementation of an auto-scaling control plane, deploying product instances and reducing developer and QA workload by 10+ hours per week; accelerated QA time by 10x
* Led the development of Attack Path Analysis, enabling companies to visualize network compromise routes taken by attackers
* Led the development of the IAM subproduct, processing 10M+ rows daily for multiple Fortune 500 customers' IAM footprints
* Pioneered development of the IAM Least-Privileged Access product, empowering customers to eliminate unused permissions from high-risk IAM users and roles
* Facilitated the onboarding of an eight-engineer team and a manager in Belfast, ensuring a smooth transition and effective collaboration
* Developed a comprehensive developer onboarding checklist, including instructional videos, now the go-to resource for onboarding 30+ engineers
* Resolved long-standing issues in the product by diving deep and applying comprehensive fixes rather than band-aids, including database weak references and multiprocessing issues

Technologies: Python, MySQL, Redis, Terraform, AWS, ECS Fargate, RDS, GCP, Azure

### Capital One — Lead DevOps Engineer (September 2018 – December 2019) · Tysons Corner, VA

* Led the DevOps team responsible for Capital One's suite of big-data management products
* Developed and maintained CI/CD pipelines for products receiving 100,000+ calls per day
* Developed a Python-based CLI deployment platform for fully automated blue-green deployments with automatic rollback, reducing deployment time from two days to 45 seconds

Technologies: Python, Terraform, Ansible, Jenkins, Amazon ECS, Docker

### Capital One — Software Developer / DevOps Engineer (April 2015 – September 2018) · Tysons Corner, VA

* Worked on a system that let Capital One customers bring third-party bank financial details into a unified mobile and web experience using Plaid and Finicity
* Developed backend code for storing and retrieving transaction data and implemented a reference web application for demonstrations and as a model for the mobile experience
* Led development of a robust CI/CD pipeline that enabled one-click blue-green production deployments
* Extended boto3 with a library for performing blue-green deployments
* Built reusable Ansible modules for deploying common infrastructure components, including Spring Boot and Python Gunicorn applications
* Built a MongoDB database migration utility inspired by Alembic
* Built a CLI application for performing MongoDB failovers across multiple regions during regional downtime

Technologies: Java 8, Spring Boot, Redis, MongoDB, Python, Ansible, Bash, Jenkins, AWS EC2

### TASC Labs — Mobile and Full-Stack Software Developer (March 2014 – April 2015) · Chantilly, VA
Worked as a mid-level software engineer on Android, iOS, web, and desktop projects within the government intelligence community.

* Led development of TESSMobile, an Android and iOS time-entry application described as the de facto industry standard within the intelligence community, downloaded over 50,000 times with 2,000+ business licenses
* Developed an Android and desktop software suite for the Defense Threat Reduction Agency that allowed forces abroad to access and search a specialized database; the source résumé reports at least 27 users
* Modernized the company website at tasclabs.com, now defunct, using Gulp, Node.js, EJS, and Bootstrap, and automated its build and deployment
* Spearheaded the lab’s migration from SVN to GitLab and drove adoption of a Git-based development workflow
* Received two Spot awards and a Mobile Development Excellence award

### Ntiva Inc. — Engineering Intern (May 2011 – January 2012) · Falls Church, VA

* Worked under the CTO developing provisioning software for Windows and Linux

Technologies: Windows PowerShell, Bash, HAProxy

## Education
**Computer Science B.S., Minor in Mathematics** — Virginia Polytechnic Institute and State University, August 2013
