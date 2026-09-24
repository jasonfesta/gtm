# Apollo search brief — solo and open-source developers

Definition: independent developers building their own products, plus open-source developers
at any employer or project size. A contributor does not have to be solo, a founder, or working
on AI. Preserve solo and open-source sublabels independently.

## Keywords

- Independent work: solo developer, indie developer, indie hacker, independent developer,
  solopreneur, bootstrapped, micro SaaS, side project, self-employed software developer.
- Open source: open source, open-source, OSS, maintainer, core contributor, committer,
  contributor, package author, library author, developer tools.
- Evidence locations: GitHub, GitLab, project maintainers page, release notes, package registry,
  personal product site, technical blog. A profile or repository URL is not an email address.

## CRM first

Ask Ops for existing solo/open-source audience labels and matching tags/role/source evidence.
Retain existing IDs and identify the evidence for independent work or an actual contribution.
Do not downgrade an employed open-source contributor because company headcount is large.

## Apollo passes

| Pass | Current title alternatives | Keyword passes, one phrase each | Company size |
| --- | --- | --- | --- |
| S1 independent builders | founder; software developer; software engineer; independent developer | indie developer; bootstrapped; micro SaaS | 1–10 as a discovery hint only |
| S2 explicit solo identity | Unset | solo developer; indie hacker; solopreneur; independent developer | Unset |
| S3 open-source builders | software engineer; developer; maintainer | open source; maintainer; core contributor; OSS | Unset |
| S4 catch missing titles | Unset | open source maintainer; package author; library author | Unset |

Suggested API body for S3, first keyword; repeat with the other listed phrases:

```json
{
  "person_titles": ["software engineer", "developer", "maintainer"],
  "include_similar_titles": false,
  "q_keywords": "open source",
  "page": 1,
  "per_page": 25
}
```

For S1 only, add `"organization_num_employees_ranges": ["1,10"]`. Small company size
alone does not prove solo status. Drop the title filter before concluding that Apollo has
no relevant profiles; maintainers may be absent from Apollo entirely.

## Qualification

Accept solo membership with attributable evidence of independently building a product.
Accept open-source membership with an attributable project, merged contribution, package,
or maintainer role. Starred repositories, generic “tech enthusiast,” recruiter profiles,
and a founder title without building evidence do not suffice. Do not add an AI requirement.

Save actual contribution/product URLs and the specific reason for the label. Return candidates
using the [shared result template](README.md#result-record-template). If Apollo misses an OSS
identity, retain the source-backed CRM record and mark Apollo match unavailable; do not infer an email.
