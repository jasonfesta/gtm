# Outreach routing criteria

This is the current person-level routing standard for every record in the CRM. Audience relevance, preferred route and readiness are separate decisions.

## Audience relevance

Include relevant developers, founders, product leaders and founding-team members working on the agent/product. Personal coding is not required. Establish who the person is, their actual relationship to the product and why the outreach is relevant. An external/client relationship must not be described as employment or founding membership. Unresolved identity or relevance stays under review rather than being silently excluded.

## Current campaign selection

Developer-peer outreach targets developers. Confirm actual relevant software-building/integration work; a founder or product title alone is insufficient. Broader CRM audience records remain intact. Apply the route ordering below after this campaign-specific fit check.

## Routes and decision order

These labels preserve research and relationship context. For the current cold sequence, email → X → LinkedIn overrides the historical social-first actions in this table; scores do not reorder available channels.

| Score / route | Criteria | Intended action |
|---|---|---|
| **3A — Contact outreach** | A documented existing professional relationship, or a specific mutual contact/shared investor with evidence connecting both sides | Continue the existing relationship or pursue an introduction |
| **3B — Location outreach** | The person is based in San Francisco or has a confirmed upcoming SF visit | Direct outreach about meeting in person |
| **2 — Social → email** | No established contact/location opportunity; correct personal X or LinkedIn account and supported recent activity | Start socially, then use email as the follow-up |
| **1 — Email** | No established contact/location opportunity; social reviewed and not the preferred usable route | Personalized email |

Check contact and location first, then social, then email. Record both 3A and 3B when both apply and choose the clearest path. The stored numeric score remains 1–3; `meeting_triggers` and `meeting_path` distinguish contact versus location. A phone/video conversation may be an agreed intermediate step; it is not an in-person meeting.

If the higher-priority checks are incomplete, route 1 is provisional, not a final email-first determination. Unknown social activity must not be called inactive. Recipient preferences and suppressions take precedence over the default order.

## Evidence needed

- **Identity/relevance:** named primary source, actual current or historical role and product connection. Record conflicting chronology.
- **Contact:** named connector or prior direct relationship, how we know them and how they connect to this person. A saved Apollo contact, newsletter, cold email or copied recipient list alone is insufficient. A target's investor list is not proof of a shared investor.
- **Location:** person-level SF statement or dated confirmed visit. Company headquarters, SF job listings or the broader Bay Area alone do not establish SF-city location.
- **Social:** exact profile, personally authored post/reply URL and absolute date. The proposed starting activity window is 30 days. Profile existence, followers, company posts and search crawl timestamps do not prove recent personal activity.
- **Email:** exact address and evidence of ownership. Guesses and provider-only results stay candidates; deliverability remains a separate check.
- **History:** relevant professional conversations, actual prior actions, recipient preferences and unresolved submissions.

Use authorized account read-only history before claiming a relationship. Searches are limited to the selected account and queried records; no result does not prove no relationship exists anywhere.

## Social followed by email

Record the chosen first social action: a reply to the actual post or a DM where available and appropriate. Preserve email as the next planned channel. The [contact rules](../runtime/Rules.md) require at least 72 hours between unanswered touches and cap unanswered outreach at three touches per person across X, LinkedIn, and Gmail, including public replies and DMs; engagement cancels the drip while actual conversation replies may continue. Before an email follow-up, inspect the actual response/history. No automatic sequence is enabled. A reply moves the person into a conversation; an opt-out stops outreach. Do not interpret this route as permission to contact both channels simultaneously.

Public replies require the actual post. Private DM replies require the incoming message. A generic social route does not create cold-DM capability in a tool that only supports comments or inbound replies.

## Record and enforce the decision

Each review stores audience fit and reason, numeric score/status, contact/location triggers, social activity evidence, preferred/secondary channel, desired outcome, source references, reviewer, date and next action. Keep readiness explicit: research needed, review needed, hold, suppressed or ready for preparation. A route does not authorize sending.

The local append-only review tables preserve old decisions as audit evidence; `latest_route_reviews` supplies the current one. The database's technical-contribution classification is descriptive and does not exclude a relevant founder/team member. Current copy context includes the latest review, and Gmail preparation rechecks readiness before external draft creation.

Validate a frozen reviewed input with `python3 -B -m crm.route_review PATH` from `runtime/`; add `--apply` only to commit the reviewed local decisions. Replaying an identical run is a no-op. Use a new run and fresh contact snapshots for changes. Keep personalized decision files private.

## Completion and measurement

Review every person in the frozen fetched batch and all three channels, including missing/blocked outcomes. The current CRM pool is not the daily research denominator. Use a small varied calibration group to check consistency before completing a larger cohort. A completed review can contain explicit holds; it does not mean everyone is contactable.

Track actual messages, human replies, attributable signups and first useful product use separately. For contact/location outreach, track introduction paths, invitations, meetings booked/held and agreed follow-up. Drafts, calls and in-person meetings are distinct events. Conversion is unavailable until actual outcomes and attribution exist.

## Daily fetched-batch workflow

Follow the selected agent runbook for scope and the saved copy workflow for preparation. Preserve contact limits, actual history and verified outcomes.

## Cold outreach order — current override

Use **email → X → LinkedIn** for the daily cold sequence, in that order among available, verified and ready channels. Hydration records exact channel identities, ownership evidence and availability; relevance scores and historical route labels do not reorder this sequence. Unavailable channels are recorded and skipped without inventing contact details. Temporary readiness, stale incoming coverage or uncertain submission remains a hold, not evidence of exhaustion.

Use each channel at most once, with at least **72 hours between confirmed actual outbound sends**. A reply, engagement or opt-out stops the cold sequence. Once available channels are exhausted, exclude the person from new cold batches; keep their CRM record and all history. Do not restart the sequence or reuse a channel to reach three touches. Actual conversations are reviewed separately. Every send still needs individual approval.
