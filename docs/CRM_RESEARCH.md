# Source-to-contact research runbook

This is the operating procedure for every new CRM source. The [routing criteria](OUTREACH_ROUTES.md) define audience eligibility and channel choice; the [System guide](CRM.md) defines provider setup and boundaries.

## 1. Inspect and freeze the cohort

Read the workspace playbook. Identify the source, format, ownership, terms/robots and size. Use a bounded inspection for an unknown-size source. Create a unique private run folder and copy the [manifest example](../runtime/config/research-run.example.json). Record original source rows, input hash, timestamp, exact inclusion criteria, denominator, methods, budget and remote-processing scope.

Do not initialize over an existing database, replay a named import script as a generic importer, or begin a full directory crawl without its required review. Back up SQLite consistently before reviewed mutations. Keep source listings separate from deduplicated company/person identities.

## 2. Resolve identity and relevance

Use official company/team pages, named personal professional pages and original announcements. Establish exact person, product, role and source date. Include relevant founders and team members without requiring personal software implementation. Record former roles, aliases and client work as such. A company page alone does not establish every employee's location or responsibilities.

Preserve contradictions. Similar names, profile photographs, badges and copied biographies are insufficient to merge people. Evidence from the same copied announcement counts once. Treat unsupported search/provider results as candidates.

## 3. Review every channel

| Channel | Acceptance evidence | Separate limits |
|---|---|---|
| Email | Exact address publicly associated with that person for professional contact | Provider status and mailbox deliverability are separate; inferred addresses remain inferred |
| LinkedIn | Explicit official/self link or strong corroboration of exact profile identity | Ownership may be supported externally while profile access is blocked |
| X | Explicit official/self link or strong corroboration of the exact account | Existence/ownership does not establish recent activity or employer |

Record an outcome for every person/channel in the frozen input batch: supported association, candidate held, blocked, not found in checked sources or conflicting. Do not add a fictitious contact row for a missing channel. Preserve exact strings while normalizing only reviewed equivalences; do not collapse different profile paths or email domains.

## 4. Determine the route

Check existing professional relationships and named mutual connections in the selected authorized account. Establish both sides of a shared investor/connector claim. Check person-level SF location or a confirmed visit. Either can support contact/location outreach.

Otherwise check dated personally authored X/LinkedIn activity; the initial proposed window is 30 days. Supported active social selects social → email. If social is not the preferred usable route after review, select email. Unknown meeting/social evidence means a provisional assignment, not inactivity. Record reason, sources, preferred channel, next action and readiness.

## 5. Optional provider work

Saved-contact search, new-person discovery, enrichment and Actor execution are different operations. Use the exact service guide. Before paid/remote work, establish the fixed cohort, current unit/plan costs, hard ceiling, requested fields, retention and explicit authorization. New runs do not inherit previous budgets.

Keep extra email/phone reveal and probing options off unless specifically requested. Provider "verified" cannot replace public ownership evidence. Apify cloud processing remains external. Stop on source access barriers; do not switch scrapers to bypass them.

## 6. Review, import and preserve history

Check a varied calibration sample, then review the full frozen cohort. Save explicit dispositions for uncertainty. Validate foreign keys, exact contact ownership mapping, duplicate handling, source references and all channel outcomes before a transactional import. Do not erase prior evidence or actual communication history.

The route importer validates exact cohort/contact snapshots and appends decisions. Technical contribution remains descriptive; `audience_fit` controls eligibility. A newer review supersedes the review system's prior queue entry while retaining its audit. Canonical contact changes require explicit public proof and a before/after record.

## 7. Report and hand off

Report source rows/downloads, unique company/person counts and each channel's candidate versus supported coverage separately. Use the fixed denominator including missing/unresolved records. Report actual provider charge and measured time only when available; no blanket success percentage or invented savings.

Deliver a private readable review, exact decision input, evidence references, attempts/failures, cost receipt, aggregate status and next actions. Use `python3 -B -m crm.coverage` for current counts; staged copies are not additional downloads. Run integrity/tests as described in [maintenance](MAINTENANCE.md).

No review/import step authorizes sending, scheduling, publishing or external draft creation. Product claims and live channel operations have their own readiness checks. See [account setup](ACCOUNTS.md) for identity checks.
