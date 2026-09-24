# Darwin GTM agent runtime

Manual-only runtime modules shared by the Darwin operation agents.

The package currently supports:

- CRM snapshots and reviewed handoff application through `ops agents`.
- Prepared PostHog handoff submission through `ops agents`.
- Agent discovery from configured indexes and directories.
- Agent Reply scanning, preparation, provider reconciliation, and receipts.

Use [`../agents/`](../../agents/README.md) for agent missions,
ownership boundaries, configuration examples, and activation requirements.
Every command is explicitly invoked; this package does not create schedules,
timers, cron jobs, or background automation.

Run the package tests from the repository root:

```sh
.venv/bin/python -B -m unittest discover -s tests -v
```
