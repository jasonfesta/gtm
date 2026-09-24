# Account setup

Provision the intended operator's existing provider connections privately. A browser session, connector and standalone adapter may have different identities; verify the actual surface used for the run.

| Surface | Verify |
| --- | --- |
| X / X Pro | Signed-in handle and selected deck/list ownership |
| Reddit | Signed-in username and subreddit participation rules |
| Hacker News | Signed-in username and exact item/comment context |
| LinkedIn / Sales Navigator | Signed-in self-profile and permitted source access |
| GitHub, Discord, Moltbook | Connected identity, permitted route and provider-required account state |
| AgentMail, Smartlead, Apollo, Apify, Portkey | Existing configured credential, account scope and available budget |
| CRM / PostHog | Configured destination identity and minimum required access |

Record expected identities and private configuration references in `runtime/accounts/`. Never commit tokens or copy another operator's browser session. Recover expired access through normal provider sign-in, then verify identity again. Authentication does not reset prior contact history or prove eligibility.

Keep browser receipts and provider readback under ignored `runtime/logs/` or run output directories. A new checkout must restore or bind its existing ledgers before any contact action. Follow [setup](SETUP.md), [shared CRM](SHARED_CRM.md), and the selected [agent runbook](../agents/README.md).
