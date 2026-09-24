# PostHog attribution implementation contract

This specifies how [UTM formation](FORMATION_RULES.md) must reach PostHog. It is a design and acceptance contract, not evidence of deployed redirects, new schema fields or complete channel tracking.

## Reporting scope

Darwin PostHog project `121185`, dashboard `2079780`, is the documented target. The current coordinated dashboard scope has seven buckets: email sends, inbound email replies, social DMs, outbound public social replies, email opens/clicks, UTM visitors and UTM signup funnel. Smartlead remains restricted to campaign `3935204`; its current display name is `gtm-dev`, while its registered website slug remains `gtm-dev-3935204`. Gmail/X/LinkedIn counts require confirmed local GTM receipts. The analytics task owns actual dashboard changes and final receipt mappings.

Do not broaden Smartlead to unrelated campaigns or change the immutable UTM slug when the display name changes.

For Smartlead website entries require `utm_source=smartlead`, `utm_medium=email`, `utm_campaign=gtm-dev-3935204` and the established production filter. New Gmail/X/LinkedIn program slugs in the formation guide require registry/propagation validation before being treated as deployed. Use an explicit source × medium × program allowlist, not all social traffic or every event with a UTM.

Profile traffic requires a separate future visitor/signup placement view and is excluded from send-based click/conversion denominators. The analytics task's current website tiles exclude profiles and retain the exact Smartlead tuple plus explicitly described future local outreach tuples. Do not count those future tuples as deployment evidence. PostHog's broad channel-type classification is separate from these precise GTM fields. Custom channel types are supported, but changing project-wide classification is a separate implementation decision. [PostHog channel types](https://posthog.com/docs/data/channel-type).

Coordinated receipt mapping from `runtime/crm/activity.py`: `workspace=gtm-dev`, `dashboard_key=gtm-dev`, `platform=gmail|x|linkedin`, `entry_type=email|dm|public_reply|inbound_reply`, and `program=gtm_outreach|casual_engagement|gtm_conversation`. These operational programs are not UTM campaigns. Keep them separate from the evergreen URL slugs; do not infer missing campaign or copy attribution. `gtm.message_sent` and `gtm.reply_received` retain opaque event/recipient keys; no bodies, emails or raw URLs belong in the export. Inspect the current receipt guide before consuming these fields.

## Traceability model

```text
Private saved draft + link revision
    → confirmed send receipt (messages only)
    → distributed link / optional registered redirect
    → observed destination landing + browser identity
    → authenticated application account
    → durable signup / product outcome

Profile link + registry revision
    → optional registered redirect
    → observed destination landing + browser identity
    → authenticated application account
    → durable signup / product outcome
```

The first path links an acquisition to a message only when a supported message-link identifier survives. The second has no recipient or send receipt. UTM fields alone give aggregate source/content attribution, not a unique message or person.

## Native properties versus application fields

| Input / record | PostHog representation | Status / rule |
|---|---|---|
| Five traditional UTMs on landing | Event properties `utm_source`, `utm_medium`, `utm_campaign`, `utm_content`, optional `utm_term` | Documented SDK capability; verify actual app initialization and first pageview. |
| First observed source | Person property such as `$initial_utm_source` | Documented; do not substitute this for every later touch. |
| Latest person source | Person property such as `utm_source` | Not a guaranteed backfill for anonymous arrivals; use explicit persisted application context for conversion snapshots. |
| Session entry attribution | Session entry UTM properties | Documented scope; verify exposed property names/query behavior in the deployed project before writing queries. |
| `utm_id` | Existing app `utmId` → `campaign_touch_key` | Existing local inspection describes aggregate semantics. Preserve; do not repurpose. Native SDK capture of this sixth field is not assumed. |
| New `gtm_link` query token | Proposed event `gtm_link_key` plus registry lookup | Not implemented. Needs parser, validation, persistence, sanitizer, exporter and conversion mappings. |
| Placement, copy revision, account key, sequence position | Proposed normalized event metadata from registered link and confirmed receipt | Not automatic from UTMs. Keep identity maps and exact copy private. |
| Actual application signup | Existing `auth.signup_completed` | Preserve durable-account-creation semantics; do not count sign-in or redirect visits as signup. |
| Activation / key creation | Existing `product.activated`, `developer.api_key_created` where their emitters are verified | Separate milestones; key creation is not successful API use. |

PostHog distinguishes event, initial/latest person and session attribution. In particular, latest person values are not automatically backfilled from an anonymous landing in all cases. Additional query parameters need configured capture or explicit events. [UTM documentation](https://posthog.com/docs/data/utm-segmentation).

## Message-level extension

Recommended design: retain aggregate `utm_id` and add a separately supported `gtm_link` token. Generate at least 128 random bits for each message-link revision, with uniqueness enforced locally. Map it to recipient/message/copy records only inside the private CRM. Do not encode or hash an email into a URL as a shortcut. Multiple links in one message have distinct keys and one shared receipt; retries retain their keys.

Use reusable placement-level link keys for public profiles, with no intended recipient. All public links use `/` or `/call`. The final destination must receive either the supported key or enough validated query attribution. A visit to `join.darwin.ai` is not automatically a field in a later `darwin.so` signup event.

Capture and validate the token explicitly in the application; resolve its registered source, medium, program and content. Unknown tokens or mismatches get `unverified`/conflict status rather than silently inheriting trusted attribution. Keep raw user-supplied UTMs separate from registry-verified values. Never turn a token into a login credential, authorization grant or PostHog customer identity.

Token possession identifies the distributed link, not the visitor. A forwarded email may acquire a different actual account. Identify that account through authentication, not by matching the intended recipient. If the extension is absent, label reporting aggregate-only; do not claim per-message conversion.

## Redirect, identity and consent propagation

`join.darwin.ai` and `darwin.so` are different registrable domains. A cookie set for one cannot simply be read as a cookie on the other. If the join host is used, allow only `/` and `/call`, preserve validated query UTMs in the final URL, and capture on `darwin.so`. Otherwise use the main domain directly. If identity truly starts on both sites, a reviewed cross-domain handoff is needed. PostHog documents separate approaches for subdomains and different domains. [Cross-domain tracking](https://posthog.com/tutorials/cross-domain-tracking), [cookie domain rules](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Set-Cookie).

The landing application must accept and persist valid acquisition context before routing strips the query, including through signup/login, external authentication and a new callback request. Keep the browser's anonymous identity connected to its authenticated account using the actual identity contract. Do not reset identity during normal signup. In-app browser → external browser and device changes can break continuity; unresolved paths remain unknown. [Attribution troubleshooting](https://posthog.com/docs/web-analytics/campaign-attribution-troubleshooting).

Respect the application's consent/persistence behavior. Consent denial or blocked analytics must not be bypassed by a redirect tracking layer. Do not insert global PostHog distinct IDs into profile URLs. Public placement links must never merge unrelated visitors. Avoid transmitting private URL query contents through autocaptured `$current_url` or replay data; implement the application's existing sanitization contract for custom tracking fields.

The local audit describes signed attribution with a 90-day cookie lifetime. That is local source evidence, not a live validation or a 90-day business attribution window. Verify actual deployed parsing, allowed values, signing and conversion event properties.

## Attribution and counting rules

Use the existing proposed reporting model: last eligible GTM landing within 30 days before conversion, retaining first eligible touch and assists separately. Here “eligible” requires accepted attribution, an observed destination landing and the documented bot/internal/test exclusions; a raw redirect request alone is ineligible. This is an explicit business rule to implement, not an assumption about PostHog defaults.

Break timestamp ties deterministically by event ID. Give each conversion one primary platform/program/link under this model. A subsequent direct visit does not erase the eligible source inside the window. Freeze signup acquisition for signup-to-activation cohorts; later assistance remains separate. Preserve acquisition event time and conversion event time in UTC. The operator's sending timezone remains independent.

Keep customer acquisition separate from acquisition of the AI/agent they interact with. A Gmail-acquired customer interacting with a Smartlead-acquired AI must retain Gmail customer acquisition. Test this explicitly; tagging the landing alone cannot fix it.

| Observation | Counting rule |
|---|---|
| Draft / planned step | Local preparation only, never a send. |
| Confirmed send | Deduplicate by platform, account and canonical provider/message receipt. Exclude Smartlead-owned mail from Gmail totals. |
| Inbound reply | Actual matched incoming evidence; distinguish human, automatic and unknown classification. Outgoing replies are sends. |
| Provider open / click | Provider-reported diagnostics with tracking-enabled coverage; not verified people or website visits. |
| Redirect request | Request-level observation, potentially scanner/prefetch; not a signup or confirmed human click. |
| UTM landing | Observed browser pageview under the accepted attribution and exclusion rules; show unique browser/person units explicitly. |
| Signup | Deduplicate durable account creation by actual application account. Preserve unknown attribution. |

Email security products scan/rewrite links, and PostHog's JS bot filtering covers known agents rather than all automation. Apply independent filtering to server redirect logs. A qualified landing is a useful observational metric, not proof of human identity. [Microsoft Safe Links](https://learn.microsoft.com/en-us/defender-office-365/safe-links-about), [PostHog troubleshooting](https://posthog.com/docs/web-analytics/troubleshooting).

Compute landing-to-signup from the same eligible landing cohort and window. Compute link-bearing-message click rates only when message-level joins exist. Profile pages lack a sent-message denominator. Missing instrumentation means unavailable, not zero. Provider snapshots are cumulative and must not be summed as daily events; keep account and campaign scopes distinct.

## Acceptance matrix

These are required implementation tests; only the synthetic formation checks listed in README are performed by this documentation work.

| Case | Required result |
|---|---|
| Gmail / Smartlead / X DM / LinkedIn DM | Exact matrix values survive to landing; Smartlead retains `gtm-dev-3935204`. |
| X profile root link without referrer | Query tags survive; final URL has X/social/profile-website; untagged root remains untagged. |
| Public route validation | Only `/` and `/call`; channel-specific and other acquisition paths rejected. |
| Call link | `/call` keeps source/medium/program/content through the verified booking handoff; a visit is not a booking. |
| LinkedIn shared button | Remains custom-button; never claims exclusively profile-originated. |
| Query fields conflict with each other or a registered link key | Reject conflicting attribution; never silently reclassify a link. |
| Existing query and fragment | Functional values and fragment preserved; one query delimiter and no duplicate UTM keys. |
| Blank/duplicate/unknown fields or merge tokens | Preparation fails with a specific correction reason. |
| Signed query | No unapproved reserialization; route-specific signature checks pass. |
| Scanner HEAD/GET before real visit | No signup or human-click increment; real later visit remains usable. |
| Provider wrapper and X `t.co` | Final values intact after actual supported redirect chain. |
| Smartlead plain text and HTML | Actual clickable target correct; provider tracking-enabled flags accurately reported. |
| Auth and signup callback | Same actual account journey; conversion context retained. |
| Consent denial / blocked JS / changed browser | Coverage loss explicit; no bypass or false person linkage. |
| Forwarded private link | Actual visitor/account separate from intended recipient. |
| Multiple links / retry / duplicate webhook | Multiple link observations, one send, one durable signup per account. |
| Later campaign and direct revisit | First touch retained, last eligible rule applied, signup cohort frozen. |
| Different customer/target-AI acquisition | Customer conversion uses customer context. |
| Profile traffic in dashboard | Included in visitor/signup placement views, excluded from send denominators. |

## Remaining blockers and delivery order

1. Verify ownership/hosting/TLS for the proposed redirect domain and the two approved `darwin.so` paths (`/` and `/call`); no domain or route verification occurred in this research task.
2. Register evergreen slugs and new placements against application allowlists, preserving existing campaigns. Agree exact new token/schema mapping without changing aggregate `utm_id`.
3. Implement the registry, validated redirect and consent-aware landing/auth/conversion propagation in the owning application. No production application was edited here.
4. Align exported receipt fields with saved link/copy revisions and implement the message-key join. The analytics task owns receipt emitters and dashboard changes; existing receipt IDs do not automatically join to product customers.
5. Run the matrix locally/staging, then obtain evidence from an explicitly authorized live path. Preserve payload/read-back receipts privately. A successful ingestion response alone is insufficient.

No outbound test, profile publication, redirect deployment or schedule activation is part of this documentation request.
