# local LinkedIn setup and readiness plan

This supplements [System guide](../../docs/ARCHITECTURE.md), [DATA_MODEL.md](DATA_MODEL.md), and [WORKFLOW.md](WORKFLOW.md). The reusable account-aware prompt is [social_operator.md](../copy/templates/social_operator.md).

Use the current agent runbooks for operating scope. Preserve the existing account-specific ledger and verify adapter identity before use.

## local folder layout

```text
linkedin/
  README.md, DATA_MODEL.md, WORKFLOW.md
  LOCAL_SETUP.md            readiness requirements and data ownership
  linkedin_operator.py      manual adapter entry point
  import_legacy.py          idempotent local-history reconciliation
  browser/runner.js         account-checking LinkedIn browser adapter
  config/account.example.json
  accounts/                actual owner settings, voices, permissions
  data/                    linkedin.sqlite3 activity ledger
  history/                 readable Markdown conversation exports
  drafts/                  a/b review exports; never treated as sent
  imports/                 original legacy logs and supplied message exports
  evidence/                permitted observations and publication confirmations
  logs/                    sanitized local run and reconciliation logs
  backups/                 consistent SQLite backups and manifests
```

Private directories are ignored and begin empty. Keep cookies/passwords outside this tree in the approved browser profile or secret store. The research CRM remains at `runtime/data/crm.sqlite3`; link reviewed recipient identities instead of replacing or copying its records.

## account binding

Each owner needs a verified account ID/profile, time zone, approved voice, limits, and permissions. Verify the actual signed-in account before using personalized history and again before submission. A newly detected account stays in preview until bound by the user. A scheduled job remains bound to its expected account; it must pause when another person logs in.

Scope every conversation, draft, permission, reservation, count, and query to the sending account. Logical separation prevents mistaken activity; it is not an access-security boundary between people sharing one operating-system account.

## implemented duplicate rules

1. Permanently prevent a second public comment on the same post from the same account.
2. The current operator supports public comments and replies to actual inbound DMs. It cannot initiate cold DMs. Retained ledger rules for historical cold sequences prevent duplicates; they do not enable a new lead/hiring campaign or change current targeting.
3. Review factual private-contact history before responding. Actual incoming messages can receive an explicitly authorized response, with one outbound response per incoming message ID.
4. The [contact rules](../Rules.md) replace lifetime author deduplication with three unanswered touches shared across channels and formats, at least 72 hours apart. Engagement cancels the drip; actual conversation responses may continue. Legacy reserved-lead lists and campaign suppressions are never imported; only do-not-contact instructions recorded prospectively for the fresh program can suppress a person.
5. Keep uncertain submissions reserved. A click, cleared composer, or optimistic legacy log is insufficient proof. Reconcile the exact text under the correct target/account before retrying or replacing it.
6. Use an account lock and transactional reservations so manual and scheduled runs cannot duplicate a target or consume the same remaining capacity. Count confirmed public actions and unresolved reservations against the cap; calculate calendar days in the account's IANA time zone.
7. Refill only confirmed failures from that run's unused same-scan reserve, within its authorized total.

These rules strengthen the generic campaign/workflow scopes in the initial data-model draft. Constraints and eligibility queries must implement them before sending is enabled.

## old messages and data to retain

Preserve exact incoming/outgoing text, account, recipient, direction, conversation, platform message ID if available, reply-to message ID, event time, observation/import time, source, and delivery evidence. Store unknown dates as unknown. Record edits/status changes separately from original messages.

Keep immutable a/b draft revisions, chosen revision, voice/configuration revision, supporting post or actual incoming message IDs, lint result, action ID, attempt history, and publication confirmation. Drafts are not sent messages.

Track history coverage as unknown, partial, or complete through a stated time for a stated source scope. Absence from partial history does not mean never contacted. Review incomplete history before responding to an actual inbound DM; unresolved context stays held.

Import from existing local logs, owner-supplied exports, manually supplied conversations, or a permitted integration. Do not bulk scrape LinkedIn inboxes. Map legacy logs only to their verified owner. Preserve original imports with hashes and stable source-row keys; identical text alone must not collapse two genuine messages.

Legacy records claiming a send without confirmation become historical reported sends: they suppress repeats but are not newly verified publications. Review contradictions between pending notes and activity logs. Re-importing the same file must create no duplicate records.

Export local histories to `history/<account-id>/<conversation-id>.md` with participants, coverage/source, chronological messages, timestamps, exact text, and verification status. Personalized histories and draft exports remain ignored. Back up SQLite consistently; do not sync the live database/WAL pair.

## build sequence and completion checks

1. Maintain the implemented account settings and storage in `runtime/linkedin/`; keep the activity database and personalized exports local here.
2. Add reviewed, repeatable history imports and Markdown exports. Verify missing-history states and namesake/alias handling with local fixtures.
3. Adapt the existing approved browser/sender to account-specific identities and validated batch input. Remove fixed candidates and owner paths. Positively verify Recent feed selection.
4. Exercise draft-only runs. Verify owner voice, actual message context, a/b revision history, all duplicate rules, and private-file exclusions.
5. Verify concurrent reservation, interrupted-run recovery, uncertain-send holds, and daylight-saving day boundaries with isolated fixtures. Live test messages require their own explicit request.
6. Support a requested bounded manual batch. Verify publication evidence and local records before declaring sending ready.
7. Schedule only on explicit request, using the supported scheduler and the same locks/history/limits. Default to draft-only; recurring public sends and private outreach need separately recorded authorization. Honor stop controls and notify only on meaningful outcomes or required action unless otherwise requested.

Existing local documentation may evolve alongside implementation. Do not treat a heading marked implemented as proof; verify its files and behavior.

## fresh-slate boundary

The original X/LinkedIn operator prompt and the legacy `blocklist`, `lead_hits`, founder, designer, investor, hiring, and reserved-queue files are not policy inputs. Stiched/Stitched has no special exclusion. The browser adapter may still be reused for session access, but the local operator performs its own eligibility and copy lint. Imported legacy send logs remain factual contact history solely to prevent duplicate contact and reconcile prior activity.
