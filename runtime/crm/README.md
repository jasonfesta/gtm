# CRM and human-agent modules

Shared database and relationship services support the current agent runbooks.
`database.py` owns the shared/private storage boundary; `relationships.py` owns
canonical interaction facts. `mcp_server.py` exposes the Ops CRM contract.

| Agent | Main modules |
| --- | --- |
| Human Discovery | `human_discovery.py`, `human_discovery_search.py` |
| Human Reply | `human_reply_agent.py`, `human_reply_capture.py`, `human_reply_planning.py` |
| Human DM | `human_dm_agent.py`, `human_dm_inbox.py`, `human_inbox.py` |
| Agent DM | `agent_dm.py`, `agent_dm_manual.py`, protocol adapters |
| Email | `email_agent.py`, `smartlead.py`, `outreach_queue.py` |
| Ops | `relationships.py`, `mcp_server.py`, `activity.py`, handoff and migration modules |

These modules do not imply a schedule. Follow [agent ownership](../../agents/README.md),
[setup](../../docs/SETUP.md), and the selected runbook before provider actions.
Private ledger names and event formats are preserved for existing history.
