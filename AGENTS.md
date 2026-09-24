# GTM contributor guide

GTM is the manually operated agent workspace described in [README.md](README.md).
Read the selected runbook in agents before changing agent behavior.

- Preserve verified provider receipts, contact suppressions and uncertain outcomes.
- Keep credentials, private ledgers, browser sessions and raw run data out of Git.
- Run make source-check before publishing source changes. Database integration
  tests require CRM_TEST_POSTGRES_URL pointing to disposable loopback PostgreSQL.
- Keep the checked-in tree current. Historical plans and run reports belong in Git history or private evidence, not active documentation.
- Open browser pages in a Codex browser panel on the right when supported.
