# UTM formation rules

Version `utm-v1`. This standard governs local link preparation for Gmail, Smartlead, X and LinkedIn across campaigns and daily batches. Formation rules are defined; redirects, new fields and conversion propagation remain implementation requirements. See [PostHog implementation](POSTHOG_IMPLEMENTATION.md).

## Required fields

Every new tracked acquisition link has exactly one of each required field:

```text
https://<verified-domain>/?utm_source=<source>&utm_medium=<medium>&utm_campaign=<registered-program>&utm_content=<content>
```

Angle-bracket fields are specification placeholders, never sendable text. A tracked link is optional: do not insert one into a message that does not need a link.

| Field | Formation rule |
|---|---|
| `utm_source` | Exactly `gmail`, `smartlead`, `x` or `linkedin`. Use the dispatcher/distribution platform, never the recipient's mailbox provider, redirect host or destination. |
| `utm_medium` | `email` for all email; `dm` for initial private social messages; `reply` for private/public social replies; `social` for profiles, original public posts and shared profile buttons. |
| `utm_campaign` | Stable registered program/campaign slug. Use an evergreen program where appropriate, not a new campaign per day, person or channel. |
| `utm_content` | `<placement>-<variant>-v<version>`. Placement comes from the table below; variant is `a`, `b` or `na`; version is a positive integer. |
| `utm_term` | Omit by default. Preserve an existing registered broad audience value when needed for compatibility. Never identity. Audience usage is a local convention, not the normal paid-keyword meaning. |
| `utm_id` | Optional existing aggregate campaign-touch key only. Never substitute a per-person or per-message identifier. |

The four source names and email/DM/reply mediums preserve the [System guide](../../docs/CRM.md). Profiles and public placements extend it. PostHog documents capture of the five traditional UTMs; additional fields need explicit capture and application propagation. [UTM documentation](https://posthog.com/docs/data/utm-segmentation).

## Channel and placement matrix

Placement appears in `utm_content` and in the future structured link registry. It separates profile distribution from direct messages.

| Distribution | Source | Medium | Placement | Example content |
|---|---|---|---|---|
| Gmail initial email | `gmail` | `email` | `email` | `email-a-v1` |
| Gmail reply/follow-up | `gmail` | `email` | `reply-email` | `reply-email-b-v1` |
| Smartlead initial email | `smartlead` | `email` | `email` | `email-a-v1` |
| Smartlead reply/follow-up | `smartlead` | `email` | `reply-email` | `reply-email-a-v1` |
| Either email system's reusable signature | Actual dispatcher | `email` | `signature` | `signature-na-v1` |
| X initial DM | `x` | `dm` | `dm` | `dm-a-v1` |
| X reply in a DM conversation | `x` | `reply` | `reply-dm` | `reply-dm-b-v1` |
| X public reply | `x` | `reply` | `reply-public` | `reply-public-a-v1` |
| X profile website field | `x` | `social` | `profile-website` | `profile-website-na-v1` |
| X separate bio-text link | `x` | `social` | `profile-bio` | `profile-bio-na-v1` |
| X original / pinned original post | `x` | `social` | `post` / `pinned-post` | `pinned-post-a-v1` |
| LinkedIn initial private message | `linkedin` | `dm` | `dm` | `dm-a-v1` |
| LinkedIn private reply | `linkedin` | `reply` | `reply-dm` | `reply-dm-b-v1` |
| LinkedIn public comment/reply | `linkedin` | `reply` | `reply-public` | `reply-public-a-v1` |
| LinkedIn Contact info website | `linkedin` | `social` | `profile-contact` | `profile-contact-na-v1` |
| LinkedIn Featured link | `linkedin` | `social` | `profile-featured` | `profile-featured-na-v1` |
| LinkedIn custom button across surfaces | `linkedin` | `social` | `custom-button` | `custom-button-na-v1` |
| LinkedIn original post / company Page website | `linkedin` | `social` | `post` / `company-website` | `company-website-na-v1` |

Use only surfaces actually available to the account. InMail, where available and authorized, uses `dm` with its delivery mode recorded locally. This matrix does not create channel capability or send authority.

A LinkedIn custom button can appear on profiles, posts, messages and search results. A common button URL cannot reveal which surface was clicked: report `custom-button`, not a profile-only visit. [LinkedIn button documentation](https://www.linkedin.com/help/linkedin/answer/a727760/add-a-link-to-the-introduction-section-of-your-profile?lang=en).

Keep UTM medium distinct from receipt entry types. The current receipt adapter uses `email|dm|public_reply|inbound_reply`; the older measurement plan used `email|dm|reply`. Map explicitly at the adapter boundary rather than renaming UTMs to follow receipt enums. Profile and post records need a separate distribution kind; never force profile traffic into message counts.

## Names, program independence and versions

New program slugs use lowercase ASCII words separated by hyphens. Reserve `always-on-outreach`, `always-on-profile` and `always-on-signature` as this specification's evergreen names, subject to application registry acceptance before production use. They do not enroll anyone in a campaign.

Use `always-on-profile` across X and LinkedIn; source/content distinguish platform and placement. Daily copy uses `always-on-outreach` unless assigned to an existing specific program. Keep batch ID, batch date, actual send time, sequence step and operator account in private metadata rather than multiplying UTM names.

Preserve the registered Smartlead campaign value `gtm-dev-3935204` exactly. Never replace it with an evergreen name or the older illustrative `gtm_developer_outreach_2026_09`. Historical registered underscore names remain valid; do not rewrite old data to match the new convention.

`a` and `b` are selected editorial alternatives; `na` means no variant comparison. `v1` represents a frozen copy/placement definition, not identical wording for every person. Exact personalized drafts and immutable revisions stay local. A/B labels alone do not establish randomized experiments.

A change to source, medium, program, placement, variant or destination creates a new link revision. A wording edit creates a local copy revision; bump the UTM version when the registered copy definition changes. Preserve previous mappings. Reuse the saved link during submission retries rather than manufacturing a new touch. Sequence steps are local structured metadata, not inferred from the UTM version.

## Public routes and profile tracking

Use only two public paths: `/` for the main destination and `/call` for booking. Channel and placement belong in query parameters. Do not create channel-specific paths, account paths or other acquisition aliases. This replaces the earlier channel-alias proposal and the broader acquisition destination list for new links.

The default direct link is `https://darwin.so/` or `https://darwin.so/call`, with the appropriate UTMs. The `/call` destination and booking handoff still require implementation verification; its inclusion here does not claim it is live.

If `join.darwin.ai` is retained as a redirect host, it must follow the same two-path structure. Preserve validated query attribution through the redirect:

```text
https://join.darwin.ai/?utm_source=x&utm_medium=social&utm_campaign=always-on-profile&utm_content=profile-website-na-v1
    → HTTP 302
https://darwin.so/?utm_source=x&utm_medium=social&utm_campaign=always-on-profile&utm_content=profile-website-na-v1
```

The query identifies the link assigned to the X profile even without a referrer. It does not prove a profile view or identify the visitor. Use different query values for DMs, replies and other placements; keep the path unchanged. An untagged root or `/call` visit must not acquire invented X attribution. Referrers may be absent or reduced to the originating domain. [MDN Referrer-Policy](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Referrer-Policy).

Redirect destinations are fixed by the two-route allowlist, never a caller-supplied `url=`. Validate and preserve one consistent set of query fields; reject duplicate/conflicting values. If a supported opaque link key is present, its registered metadata must match the supplied tags. Untagged traffic stays untagged. Use HTTPS, a short redirect chain and a temporary 302 with `Cache-Control: no-store`; DNS alone does not preserve or generate tracking context. A redirect request remains separate from a destination pageview. [MDN 302](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Status/302).

## URL assembly rules

1. Start with a verified HTTPS destination on `/` or `/call` and its allowed parameter contract. Record destination as `main` or `call`, independently of source. Do not add results, bot or other public acquisition paths.
2. Store the original destination verbatim. Preserve path casing, approved query values and fragments. Never lowercase a whole URL.
3. Require registered source/medium/content values. Missing values, typos and placeholders block preparation; do not guess or silently invent categories.
4. Put UTMs before `#fragment`. Use `?` to start the query and `&` to extend it. Encode new keys/values once using a URL library. Never concatenate raw copy or private search text. [MDN URLSearchParams](https://developer.mozilla.org/en-US/docs/Web/API/URLSearchParams).
5. Allow exactly one of each reserved UTM key. An identical existing set is a no-op. Conflicting or duplicate keys, blank required values, unknown `utm_*` keys and unresolved merge fields require correction before saving a ready link.
6. Signed or byte-sensitive URLs require their approved construction method. Parsing and reserializing an existing query may invalidate signatures; do not rewrite speculatively.
7. Add fields in source, medium, campaign, content, optional term, optional id order for consistent review. Preserve existing functional query components. Ordering is a workspace convention, not an analytics requirement.
8. Default to a direct tagged destination. Use the optional join-domain redirect only on `/` or `/call`. Avoid discretionary shortener chains over provider wrappers and never implement an open redirect.
9. Do not add acquisition UTMs to internal navigation or authentication links. Persist accepted acquisition context through the application.
10. Keep names, email addresses, handles, recipient companies, sequential CRM IDs, credentials and private message text out of tracking URLs. Opaque identifiers remain linkable data, not proof of visitor identity.

Illustrative syntax only; no destination behavior is asserted:

```text
Before: https://darwin.so/?view=compact#start
After:  https://darwin.so/?view=compact&utm_source=gmail&utm_medium=email&utm_campaign=always-on-outreach&utm_content=email-a-v1#start
```

## Complete channel examples

These are specification examples, not production-ready links. Destination and propagation checks still apply.

```text
Gmail initial email
https://darwin.so/?utm_source=gmail&utm_medium=email&utm_campaign=always-on-outreach&utm_content=email-a-v1

Smartlead existing GTM Dev campaign
https://darwin.so/?utm_source=smartlead&utm_medium=email&utm_campaign=gtm-dev-3935204&utm_content=email-a-v1

X initial DM
https://darwin.so/?utm_source=x&utm_medium=dm&utm_campaign=always-on-outreach&utm_content=dm-a-v1

X public reply
https://darwin.so/?utm_source=x&utm_medium=reply&utm_campaign=always-on-outreach&utm_content=reply-public-a-v1

LinkedIn initial DM
https://darwin.so/?utm_source=linkedin&utm_medium=dm&utm_campaign=always-on-outreach&utm_content=dm-b-v1

LinkedIn Contact info website
https://darwin.so/?utm_source=linkedin&utm_medium=social&utm_campaign=always-on-profile&utm_content=profile-contact-na-v1

Gmail call invitation
https://darwin.so/call?utm_source=gmail&utm_medium=email&utm_campaign=always-on-outreach&utm_content=email-a-v1
```

## Daily copy application

For every link-bearing draft in the [daily workflow](../copy/WORKFLOW.md), select destination, dispatcher, placement, registered program and variant. Construct once, validate, and save the exact URL alongside its original destination and draft revision. Review rendered hyperlink targets and channel character limits as part of copy review.

Save contract version, batch ID/date, account key, local person/message keys, sequence position, copy revision, source/medium/program/content, link revision and intended destination locally in `runtime/`. Personalized manifests never belong in `UTM/`. Distinct links in one message get distinct link records but share one send receipt.

Refresh day 2/3 context and review before the due send. Sequence position follows the person's channel order; do not assume X always means day 1. The daily LinkedIn/X/Gmail cohort and Smartlead email campaign retain separate enrollment decisions.

New reply links use the replying dispatcher and reply placement. Quoted prior links keep their original tags. A manual Gmail reply after Smartlead is a Gmail touch with the actual conversation history linked locally. Smartlead messages sent through Gmail mailboxes remain Smartlead sends, counted once.

Smartlead merge-field copy receives the complete reviewed URL. Inspect rendered plain-text URLs and HTML hrefs. Provider tracking is independent of UTMs; changing this guide does not change provider settings. [Smartlead tracking settings](https://helpcenter.smartlead.ai/en/articles/276-how-to-enable-open-rate-tracking-in-smartlead).

Production use requires the [implementation acceptance checks](POSTHOG_IMPLEMENTATION.md). This standard does not publish profiles, deploy redirects, send messages, activate campaigns or broaden dashboard filters.
