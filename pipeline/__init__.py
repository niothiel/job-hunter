"""Job Hunter Pipeline — LangGraph-based job application automation package.

Structure:
    - infrastructure/  — core modules: config, state, paths, graph, lifecycle,
                         LLM interface, file ops, stores, schema, delta sync,
                         feasibility checker, notifications.
    - steps/           — pipeline step nodes (step1 through step11), one per
                         workflow stage. step8_veracity handles in-loop
                         truthfulness review; step10_final_veracity is the
                         post-loop final gate (ADR-0018).
    - helpers/         — standalone CLI tools: count_lines, fetch_jds,
                         list_feasible_jobs, md_to_pdf, pii_scrub, serve,
                         smoke_test_pipeline.
    - __main__.py      — entry point (run via: python3 -m pipeline)

See docs/adr/ for architectural decisions.
"""
