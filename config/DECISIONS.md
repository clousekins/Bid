# Locked product decisions for POONG rebuild

1. seq_no assigned on successful insert
2. Folder naming: `{seq}-{Company}`
3. Same company skipped only if status=applied — the constraint is per
   (persona, company), so each profile has its own pool
4. LLM: OpenAI gpt-5.6-luna (configurable)
5. Auto-submit OFF by default
6. Hybrid rejected on non-manual deep-scan paths
7. Associate/junior titles rejected
8. DB: SQLite
9. UI: React + FastAPI
10. Ingest: manual JSON/CSV **and** board discovery (`/api/sync/discover*`).
    Discovery is live, not 410 — it was re-enabled and is now the main
    source of new listings alongside manual apply.
11. Cover letter generated with resume
12. Apply attach: UI `window.open` + CDP; no blank tab
13. Mark applied ends Playwright apply session
14. Deep-scan and tailor run as async background jobs with progress polling
15. Every persona-specific answer comes from that persona's own ground
    truth. A value we do not have is omitted so the field escalates to the
    LLM or the operator — never filled from another persona
16. Applying is not the outcome. Each submitted package records what the
    employer did next; response rate is measured against resolved
    applications, excluding those still waiting
17. Every submission is screenshotted before and after the submit click
18. ATS board tokens are added only after returning live jobs from the
    public API. Guessed slugs become 404s that clog the catalog
