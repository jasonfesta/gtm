# Email audience sourcing — CRM + Apollo search draft

Use these audience definitions for bounded sourcing. These are completed search
briefs, not completed prospect acquisition or deliverability checks. Email stays parked after
all other work today. Do not enroll recipients or activate Smartlead as part of this plan.

## Audiences and keyword searches

| Audience | Jason's definition | Primary discovery keywords | Brief |
| --- | --- | --- | --- |
| Solo and open-source developers | Independent builders and all open-source developers, including employed contributors | indie developer, solo developer, solopreneur, bootstrapped, micro SaaS, open source, maintainer, contributor, OSS | [Solo + open source](solo-open-source-developers.md) |
| Assistant developers | People producing assistants at companies | AI assistant, conversational AI, AI agents, agentic workflows, copilot, tool calling, MCP, LangGraph, OpenAI Agents SDK | [Assistant developers](assistant-developers.md) |
| Knowledge workers | People interested in early adoption of AI technology | AI early adopter, experimenting with AI, AI pilot, beta tester, AI workflows, AI productivity, ChatGPT, Claude, Perplexity | [Knowledge workers](knowledge-workers.md) |

Keywords retrieve candidates; evidence establishes audience membership. A tool mention alone
is insufficient. Audiences may overlap: retain every supported label while deduplicating the
person. Do not require AI interest for the open-source developer audience.

## Search together, in this order

1. Ask Ops to search existing CRM audience classifications, role descriptions, tags, and source
   evidence using the audience briefs. Jason confirms members already exist in CRM. Return
   aggregate coverage and eligible identity references through authorized Ops handoffs; do not
   copy the whole CRM into these Markdown files. Stored classifications are not fresh qualification.
2. Use each brief's small Apollo keyword passes. Start with one page of 25 per pass; review
   relevance before broadening. This is a proposed manual review batch size, not a quota or schedule.
   Keep geographic, seniority, funding, and industry restrictions unset unless a pass says otherwise.
3. Use Apollo person ID and exact LinkedIn profile as discovery identity keys. Have Ops match to
   canonical CRM person/contact IDs before calling anything net new. Preserve existing CRM records.
4. Record the candidate's role/project or early-adoption evidence, source URL, observation time,
   and the search pass that found them. Keep private results in ignored `outputs/`, not in Git.
5. Separate counts for retrieved, evidence-qualified, existing CRM, net-new CRM, address available,
   provider-verified address, suppressed, and ready for copy. Do not combine these into “leads sent.”
6. Only when email execution resumes: obtain actual email verification evidence, current Ops
   suppression context, and final per-recipient subject/body/copy_version. The email agent imports
   supplied final copy; these documents are not outreach copy. CRM and PostHog calls remain Ops-only.

## Apollo mechanics

Use People search with current job-title filters; title alternatives broaden results. In API
examples, each `q_keywords` phrase is a separate pass. Do not send the prose OR keyword bank
as if Apollo's keyword parameter guaranteed Boolean interpretation. Apollo documents Boolean
operators for the UI job-title filter separately. Suggested titles and keywords below are
our search hypotheses, not Apollo taxonomy IDs. A missing UI option is not a reason to invent a filter.

The People API Search endpoint returns prospect metadata, not email addresses. An email-status
filter is a search hint; retain actual returned address/status evidence before asserting a verified
route. Search and enrichment are separate operations. This document does not run enrichment.

- [Apollo People API Search](https://docs.apollo.io/reference/people-api-search)
- [Apollo people-search filters](https://knowledge.apollo.io/hc/en-us/articles/4412665755661-Use-Search-Filters-to-Find-Prospects)
- [Apollo Boolean job-title searches](https://knowledge.apollo.io/hc/en-us/articles/25826173417485-Search-Job-Titles-with-Boolean-Operators)

## Result record template

```yaml
audience_labels: []
search_pass: ""
apollo_person_id: ""
linkedin_url: ""
name: ""
company: ""
current_role: ""
qualification_reason: ""
evidence_url: ""
evidence_excerpt: ""
observed_at: ""
crm_match_status: pending_ops
canonical_person_id: null
canonical_contact_id: null
email_status: not_retrieved
email_verification_evidence: null
suppression_status: pending_ops
copy_status: not_prepared
```
