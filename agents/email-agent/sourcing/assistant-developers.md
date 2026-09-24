# Apollo search brief — assistant developers at companies

Definition: people producing or integrating AI assistants at companies. Require an attributable
company assistant, copilot, agent, or assistant-building contribution. “Assistant” administrative
titles and generic AI interest do not establish this audience.

## Keywords

- Products: AI assistant, AI assistants, conversational AI, enterprise assistant, virtual agent,
  copilot, customer support AI, internal assistant, AI agent, agentic workflow.
- Building: tool calling, function calling, agent orchestration, retrieval augmented generation,
  RAG, model context protocol, MCP, assistant integration.
- Implementation signals: OpenAI Agents SDK, Anthropic SDK, Vercel AI SDK, Google ADK,
  LangChain, LangGraph, Pydantic AI. Treat these as phrases, not unverified Apollo technology IDs.

## CRM first

Ask Ops for existing assistant-developer classifications, current employer relationships, and
assistant/project evidence. Match the person and company, not just a shared company keyword.
Keep solo assistant builders as an additional solo label where supported; do not discard overlap.

## Apollo passes

| Pass | Current title alternatives | Keyword passes, one phrase each |
| --- | --- | --- |
| A1 direct builders | AI engineer; software engineer; machine learning engineer; applied AI engineer | AI assistant; conversational AI; copilot |
| A2 framework builders | software engineer; AI engineer; full stack engineer | LangGraph; OpenAI Agents SDK; Vercel AI SDK; Pydantic AI |
| A3 team owners who build | founding engineer; CTO; head of AI; engineering manager | AI assistant; agentic workflow; AI agents |
| A4 missing-title recall | Unset | assistant integration; agent orchestration; tool calling |

No headcount ceiling: assistants are produced at companies of many sizes. Current company
association must be corroborated during review. Senior leadership qualifies only with evidence
of producing assistants, not solely supervising a generic AI budget.

Suggested API body for A1, first keyword:

```json
{
  "person_titles": ["AI engineer", "software engineer", "machine learning engineer", "applied AI engineer"],
  "include_similar_titles": false,
  "q_keywords": "AI assistant",
  "page": 1,
  "per_page": 25
}
```

## Qualification

Require a company product page, engineering article, demo, repository, or authored technical
post connecting the person to an assistant. Record assistant name, company, contribution and
source. Exclude executive assistants, personal assistants, assistant professors without relevant
building evidence, recruiters and generic AI commentators. A company selling AI does not prove
that every employee builds assistants.

Use the [shared result template](README.md#result-record-template). For later outreach preparation,
follow the existing [developer outreach brief](../../../runtime/copy/brief.md): the supplied offer is
free API access to find AIs on the open web. Do not invent pricing, quotas or compatibility.
No email copy or campaign enrollment is produced by this search brief.
