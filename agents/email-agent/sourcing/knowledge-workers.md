# Apollo search brief — early AI adopters

Definition: knowledge workers interested in early adoption of AI technology. This is behavioral,
not a fixed job title. Seek evidence of trying, evaluating, piloting or sharing new AI tools in
their work. Being a manager, designer, analyst or marketer alone does not qualify someone.

## Keywords

- Adoption intent: AI early adopter, early adopter, experimenting with AI, exploring AI,
  testing AI tools, AI pilot, AI beta, beta tester, generative AI adoption, AI champion.
- Work context: AI productivity, AI workflows, AI automation, AI research, AI for work,
  AI-assisted research, AI-assisted writing, AI-assisted design, AI prototyping.
- Tool signals: ChatGPT, Claude, Perplexity, Copilot, Gemini, Cursor, Replit Agent, Windsurf.
  Combine a tool mention with actual work or adoption evidence; generic discussion is insufficient.

## CRM first

Ask Ops for knowledge-worker classifications and early-adoption evidence in tags/source records.
Separate “already labeled knowledge worker” from “has evidence of early AI adoption.” Jason says
existing CRM members fit this audience; verify evidence without bulk relabeling.

## Apollo passes

| Pass | Current title alternatives | Keyword passes, one phrase each |
| --- | --- | --- |
| K1 product/design adopters | product manager; product designer; UX researcher; design lead | AI workflows; generative AI; AI prototyping |
| K2 research/operations adopters | research analyst; business analyst; operations manager; consultant | AI productivity; AI automation; AI pilot |
| K3 writing/marketing adopters | content strategist; marketing manager; writer; communications manager | AI-assisted writing; ChatGPT; Claude |
| K4 intent-first recall | Unset | AI early adopter; experimenting with AI; AI champion; testing AI tools |

These title groups are discovery starting points, not exclusions. Leave company size, seniority,
industry and geography unset initially. Expand titles when direct early-adoption evidence points
to another knowledge-work role. Company-level AI keywords are hints, not personal adoption proof.

Suggested API body for K4, first keyword:

```json
{
  "q_keywords": "AI early adopter",
  "page": 1,
  "per_page": 25
}
```

## Qualification

Require an attributable post, portfolio, case study, event contribution or work artifact showing
interest in early AI adoption. Prefer dated evidence and record its date; do not invent a hard
recency cutoff. Reject keyword-only profiles and vendor promotion with no personal adoption
signal. Technical builders may qualify for another audience too; preserve supported overlap.

Use the [shared result template](README.md#result-record-template). An early-adopter label does
not imply consent to email, a verified route, or a specific developer API need. The assistant-dev
free API offer must not be automatically copied to this audience without a relevant supported use.
