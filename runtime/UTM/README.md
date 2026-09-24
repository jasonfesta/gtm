# UTM link construction

Version `utm-v1`. The reusable formation standard is complete for local copy preparation; production redirects and end-to-end propagation remain unverified.

## Purpose

Define consistent UTM links for Gmail, Smartlead, X and LinkedIn, including private messages, public replies, profile links and redirects. Keep reusable rules here and personalized drafts and attribution records locally in `runtime/`.

| Guide | Use |
|---|---|
| [Formation rules](FORMATION_RULES.md) | Canonical daily copy input: exact fields, source/medium matrix, placements, names, examples and URL assembly. |
| [PostHog implementation](POSTHOG_IMPLEMENTATION.md) | Event/identity mappings, redirect design, measurement rules, acceptance checks and remaining blockers. |

The copy task loads `FORMATION_RULES.md` through `copy_context` as `utm_formation_rules`. Evergreen program names are specified here but require application registration before production use. Preserve the registered Smartlead `utm_campaign=gtm-dev-3935204`, independently of its `gtm-dev` display name, and keep aggregate `utm_id` semantics.

## Public URL structure

Use only the main domain (`/`) and `/call`. Identify Gmail, Smartlead, X, LinkedIn and placements through query parameters. The optional join-domain redirect follows the same two-path rule and preserves validated UTMs. Untagged visits remain untagged.

## Existing workspace requirements

- Use the verified destination that supports the message's actual offer. The product-search example and signup destination still require validation before use.
- Preserve the exact destination and existing URL values when preparing a tracked version; retain the original alongside the final link in the private draft record.
- Keep names, email addresses and other recipient identity out of tracking parameters. Any recipient attribution uses opaque keys with the identity mapping stored locally.
- Review the final link with the saved copy revision, including the message's character count. Refresh context and review again before a later day's authorized send.
- Keep drafts, sends, clicks, replies, signups and activations as separate outcomes. A constructed link does not establish tracking implementation or a measured conversion.

These requirements come from the [CRM operating guide](../README.md) and the daily workflow. This guide does not activate outreach or schedules.

## Validation and readiness

All four UTM guides and ten current URL examples pass local checks: only `/` and `/call`, no channel paths, no duplicate query fields, required tags present, and the Smartlead campaign slug preserved. Receipt: `runtime/logs/utm-20260910/two-route-validation.json`.

Redirects, actual message rendering, deployed tracking and signup/booking propagation still require verification. The [implementation guide](POSTHOG_IMPLEMENTATION.md) lists those checks. These Markdown rules do not deploy routes or change production.
