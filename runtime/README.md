# Shared runtime

This directory contains the working CRM and human-agent implementation used by
[the eight agents](../agents/README.md). The CRM is a shared service owned by Ops.

| Location | Purpose |
| --- | --- |
| [Agent and Ops tools](gtm_agent_runtime/README.md) | Discovery, replies and separate CRM/PostHog handoffs. |
| [crm/](crm/README.md) | Database adapter, relationships, provider clients, agent helpers and reconciliation. |
| [sql/](sql/README.md) | Shared and private ledger schemas and migrations. |
| [linkedin/](linkedin/README.md) | Account-aware browser integration and private ledger. |
| [x/](x/README.md) | X account configuration and search inputs. |
| [config/](config/README.md) | Portable runtime configuration examples. |
| [copy/](copy/README.md), [UTM/](UTM/README.md) | Runtime-consumed copy and attribution policies. |
| [tests/](tests/README.md) | Synthetic and isolated database tests. |

Run modules from this directory with `../.venv/bin/python -B -m crm.MODULE`.
Run the full suite from the repository root with `make source-check`.

Current ownership and execution rules live in the agent runbooks. Read the [CRM guide](../docs/CRM.md) for storage and handoffs and [setup](../docs/SETUP.md) for provisioning.

The shared PostgreSQL CRM stores canonical relationship facts. Private data,
account bindings, copy and evidence remain ignored local state. See the
[setup guide](../docs/SETUP.md) before moving an existing
operator checkout; source renaming does not migrate its private ledgers.
