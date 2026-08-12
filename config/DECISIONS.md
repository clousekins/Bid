# Locked product decisions for POONG rebuild

1. seq_no assigned on successful insert
2. Folder naming: `{seq}-{Company}`
3. Same company skipped only if status=applied
4. LLM: OpenAI gpt-4.1 (configurable)
5. Auto-submit OFF by default
6. Hybrid rejected on non-manual deep-scan paths
7. Associate/junior titles rejected
8. DB: SQLite
9. UI: React + FastAPI
10. Ingest: manual JSON/CSV (discovery disabled / 410)
11. Cover letter generated with resume
12. Apply attach: UI window.open + CDP; no blank tab
13. Mark applied ends Playwright apply session
14. Deep-scan and tailor run as async background jobs with progress polling
