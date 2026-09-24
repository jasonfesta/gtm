# Per-run readiness

All eight agents are manually invoked. Before a run, use its canonical runbook and verify the inputs relevant to that role:

- Correct signed-in account or configured provider identity and current access.
- Exact source, recipient and parent conversation; current route and permission evidence.
- Complete enough history and suppression context to make the requested decision.
- Durable local state, bounded workload and sufficient provider budget.
- Separate CRM and PostHog handoff paths and recoverable attempt identifiers.

Discovery does not contact recipients. Email remains parked until explicitly invoked with eligible recipients and final supplied copy. A provider request, test fixture or accepted analytics submission is not a verified end-to-end outcome.

After a run, preserve exact outcome readback or an explicit uncertain/failed state. Ops reconciles destinations independently. Never re-send to repair a missing receipt before checking the provider.
