# Set up your workspace

GTM runs locally in Codex with Python 3.12 or later. Connect only the services needed by the agent you choose; you do not need every provider to begin.

## 1. Install

```sh
git clone https://github.com/darwin-studios/gtm.git
cd gtm
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

The editable install makes the CRM and agent tools available from this checkout. Keep the checkout in place while using them.

Open the checkout as a Codex project. The startup skills in `.agents/skills/` become available in that project. Node.js is needed for the LinkedIn browser adapter and WebMCP tooling.

## 2. Connect your workspace

Follow [account setup](ACCOUNTS.md) to verify the intended identities. Configure the existing shared CRM using [shared CRM setup](SHARED_CRM.md). Actual account settings and credential references belong in ignored `runtime/accounts/`; examples in `runtime/config/` and `agents/config/` contain no authentication.

This checkout does not provision a CRM service or provider subscriptions. Shared CRM access must come from the workspace owner. For an existing installation, stop local writers and back up or rebind the existing private database, account configuration and channel ledgers under `runtime/` before running agents. Never initialize over existing history; preserve uncertain attempts and suppressions.

## 3. Start one agent

Choose a [runbook](../agents/README.md) and give the task a bounded scope. For example, `start human reply agent` selects one of X, Reddit, Hacker News or LinkedIn and follows that platform's workflow. Each run verifies its own account and required history.

Keep run outputs, provider receipts and private evidence locally. Producers pass separate CRM and PostHog handoffs to Ops; a successful action at one destination does not prove completion at the other.

Read [architecture](ARCHITECTURE.md) for the complete flow and [maintenance](MAINTENANCE.md) when developing, backing up or repairing the workspace.
