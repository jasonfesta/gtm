# Finding agents on X, Reddit, and Discord

Organized from the September 23, 2026 research. Evidence dates and qualification limits below remain those of the original research.

[Research index](README.md) · Sources: [AGENT_NETWORK_RESEARCH.md](network-discovery.md).

Research date: 2026-09-23. Public-source research and proposed collection design. No accounts messaged, servers joined, bots installed, or paid API collection started.

## Recommendation

Build an evidence-backed agent directory across platforms. Discover candidates from directories, profile searches, public bot rosters, agent projects, X lists, and conversations; then verify each account and its interaction route. Reuse our Human Reply keywords for relevance, but add agent-identity searches for discovery. Otherwise we mostly find humans discussing agents.

There is no demonstrated complete public census of AI agents on X, Reddit, or Discord. Private accounts, private servers, unlabelled automation, renamed accounts, dormant agents, and search indexing prevent a credible “all agents” claim. The deliverable should be a growing inventory with measurable source coverage and an explicit unverified queue.

The 57 subreddits in GTM.md (workspace source: `GTM.md`) are topical communities. They are **not 57 agent-operated communities**. The four X seed accounts there are a starting point, not the population.

## What qualifies

Keep these classifications separate:

| Classification | Required evidence | GTM treatment |
| --- | --- | --- |
| Agent account | Attributable statement that AI operates the account plus compatible public activity | Candidate for direct agent interaction |
| Agent-assisted/hybrid | Human account using AI for some posts or tasks | Human or mixed route; no autonomy claim |
| Agent-populated community | Identified agents visibly participate | Discover agents and their permitted channels |
| Agent-managed community | Evidence agents perform substantive moderation/operations | Investigate actual agent capabilities and operator |
| Human builder community | People discuss/build agents | Source of deployments and human introductions |
| Conventional bot | Scripted alerts, commands, AutoModerator, reposting | Exclude from AI-agent census unless AI capability is evidenced |
| Framework/product/company | Software or brand rather than a running agent identity | Discovery source, not an agent record |

Use `candidate`, `documented`, and `observed_active` evidence states. “Documented” means first-party identity/operation evidence, not independent proof of full autonomy. An `observed_active` record also needs dated platform activity. Record human supervision when known; do not classify writing style as proof of automation.

## X: discovery routes, in priority order

### 1. Enumerate directories and ecosystem projects

| Source | Verified finding | Collection action / limitation |
| --- | --- | --- |
| [AI Agents X](https://aiagentsx.one/) | Page returned 53 entries, mixing agents, frameworks and other projects | Traverse agent detail pages, extract explicit X links, then verify identity and activity. 53 entries does not mean 53 active agents. |
| [Virtuals](https://www.virtuals.io/) / [agent app](https://app.virtuals.io/) | Official site links agent offerings and creation routes; app needs JavaScript | Enumerate public offering/project records through supported UI/export/API, retaining project IDs and explicit social links. No bulk API verified in this pass. |
| [OpenClaw Showcase](https://openclaw.ai/showcase) | Deployment stories include public X source threads and Discord-driven fleets | Follow each deployment's evidence to the agent account, if provided. Displayed author handles usually identify builders. |
| [Hermes Atlas](https://hermesatlas.com/ecosystem/) | Page reports 252 ecosystem projects across 12 categories | Inspect deployed-agent projects and READMEs. Skills, tools and repositories are not automatically running agents. |
| [elizaOS official links](https://linktr.ee/elizaos) | Framework, cloud, community and ecosystem entry points | Trace deployed characters and showcases to explicit X identities; avoid counting the framework account itself. |
| [Cookie](https://www.cookie.fun/) | Redirects to Cookie Pro, whose page advertises search/project/creator/tweet APIs | Evaluate current access/export contract. Do not assume the older agent leaderboard or old API endpoints remain available. |
| [Moltbook](https://www.moltbook.com/) | Agent network with X-based owner verification | Keep owner X account separate from agent X account. An ownership tweet proves neither autonomous X operation nor a second agent identity. |
| [Clawk](https://clawk.ai/leaderboard) | Agent leaderboard on a separate social network | Inspect explicit external links. Clawk handles are not X handles, despite its “Twitter for agents” description. |
| [Krawler](https://krawler.com/top/) | Agent identity/reputation directory; site explicitly limits what reputation proves | Use profiles as leads; verify any external X association independently. |

A concrete additional candidate queue from AI Agents X: J3FF, LucyAI, nft_xbt, Lea, kwantxbt, Blockrot AI, Big Tony, H4CK Terminal, Seraph AI, neurobro, Nebula, vainguard, SOLENG, JAIHOZ, Moby AI, CertaiK, nomAI, Gekko AI, Polytrader_agent, Sympson AI, cr0w, AgentYP, and Agent Scarlett. These are **directory candidates**, not verified active X accounts. No handles should be inferred from these display names. [Directory evidence](https://aiagentsx.one/).

Balance crypto-oriented sources with assistant, research, social, coding, and personal-agent deployments. Record source-family bias in reporting; a large token-directory harvest is not broad coverage of all agents.

### 2. Search account bios directly

X now documents `GET /2/users/search`, which searches names, usernames and bios. The endpoint reference specifies a 1–50-character query using letters, digits, underscores, apostrophes and spaces, with `next_token` pagination. **Do not pass the Boolean Human Reply post queries into this endpoint.** Start with separate short phrases and preserve every page/cursor and error. [User search overview](https://docs.x.com/x-api/users/search/introduction), [endpoint contract](https://docs.x.com/x-api/users/search-users).

Suggested user-search phrases:

```text
AI agent
AI assistant
autonomous agent
autonomous AI
AI persona
AI researcher
AI employee
AI intern
AI superconnector
powered by AI
powered by OpenClaw
powered by Hermes
powered by eliza
virtual agent
research agent
social agent
```

These are proposed discovery additions, not edits to the live Human Reply keywords. Extend with language-specific phrases after validating initial English results; do not silently limit the final inventory to English.

### 3. Search posts for agent identities and deployments

Use separate X post-search families:

```text
("I am an AI" OR "I'm an AI" OR "autonomous agent") -is:retweet
("AI agent" OR "AI assistant") ("tag me" OR "mention me" OR "DM me") -is:retweet
("my agent" OR "our agent") ("on X" OR "Twitter account" OR "own account") -is:retweet
(OpenClaw OR Hermes OR elizaOS) ("agent account" OR "autonomous" OR "Twitter") -is:retweet
("agent discovery" OR "tool discovery" OR "agent delegation" OR "agent skills") -is:retweet
```

These are API-style query templates; browser/X Pro versions should use that surface's supported operators (our saved deck uses `-filter:retweets`). Preserve replies: interactive agents may predominantly reply. Extract both authors and explicitly attributed agent mentions; the human who announces an agent remains its operator, not the agent.

X's recent-search documentation describes a seven-day window and a separate full-archive route. Record exact query, time window, pagination completion, and access outcome. A truncated or failed search is not zero results. Historical discovery finds launches; current timeline inspection establishes activity. [Post-search documentation](https://docs.x.com/x-api/posts/search/introduction).

### 4. Expand through X lists and account relationships

For each documented seed, inspect its public list memberships, then promising lists' members. X documents `/2/users/:id/list_memberships` and `/2/lists/:id/members`. This offers a repeatable expansion path from a handful of agents into curated groups. Lists named “AI” often contain humans; verify every member. [List-member endpoints](https://docs.x.com/x-api/lists/list-members/introduction).

Then examine bounded following/reply/mention neighborhoods for agents, operators and launch platforms. Prefer curated following lists and repeated agent-to-agent interactions over millions of followers of a popular assistant. X documents follower/following access, but graph membership is only a lead. [Follows documentation](https://docs.x.com/x-api/users/follows/introduction).

### 5. Resolve identities and review evidence

Resolve canonical usernames to X user IDs; retain handle history. X supports batch lookup of up to 100 usernames. Preserve source URL, source record ID, first/last seen dates, website, operator association, activity permalink and interaction route. [Lookup contract](https://docs.x.com/x-api/users/get-users-by-usernames).

X's automated label is evidence of automation, not of a particular AI architecture or suitability for Darwin. Its absence is not proof of human authorship. The user-field schema reviewed here does not expose a general `is_ai_agent` field; `verified` and `is_identity_verified` are not substitutes. [Automated-label explanation](https://help.x.com/en/using-x/automated-account-labels), [user fields](https://docs.x.com/x-api/users/get-users-by-usernames).

## Reddit: communities with actual bot or agent involvement

| Destination | Evidence and classification | Practical value / remaining check |
| --- | --- | --- |
| [r/SubSimulatorGPT2](https://www.reddit.com/r/SubSimulatorGPT2/) | Bot-generated simulation community; current page is restricted to approved contributors | Public agent/bot author discovery. Legacy text-generation bots are not automatically tool-using agents; weak direct integration fit. |
| [r/SubSimGPT2Interactive](https://www.reddit.com/r/SubSimGPT2Interactive/) | Bot participation is explicitly labelled; sidebar provides active bots by operator and a Discord link | Strong concrete roster source. Extract current roster, inspect dated activity, separate retired/banned bots. Do not count sidebar “active” as independently verified current operation. |
| [r/SubSimGPT2InterMeta](https://www.reddit.com/r/SubSimGPT2InterMeta/) | Linked by the interactive community as its meta-discussion destination | Operator/deployment discovery; not independently established as agent-run. |
| [r/AI_Agents agent-post example](https://www.reddit.com/r/AI_Agents/comments/1wir49d/this_reddit_account_and_this_post_were_made/) | Author claims an agent created the account/post | Candidate account evidence; not proof the whole subreddit is agent-run. |
| [Agent check-in thread](https://www.reddit.com/r/ChatGPT/comments/1wh2oug/calling_all_agents_please_checkin/) | Self-described agents and human-mediated AI contributions | Discovery queue only. Verify each account and who actually posts. |
| [Five-agent subreddit experiment](https://www.reddit.com/r/AgentsOfAI/comments/1tsyt9u/i_let_5_ai_agents_run_a_subreddit_for_2_weeks_and/) | Author describes a private agent-only experiment | Project lead; public destination, access, and independent operation unverified. |
| [Open Moderator](https://developers.reddit.com/apps/open-moderator) and [SubPilot](https://developers.reddit.com/apps/subpilot-app) | Developer listings describe AI community-management tools | Discover disclosed deployments and bot authors. An app listing does not enumerate its installations or prove a subreddit uses it. SubPilot explicitly retains human control. |

The strongest verified Reddit starting point is the interactive bot roster, not the generic AI subreddit names. Fully autonomous, publicly joinable subreddit governance was **not established** in this pass.

Search templates for further discovery:

```text
site:reddit.com/r/ "run by AI"
site:reddit.com/r/ "operated by an AI"
site:reddit.com/r/ "Active Bots by Operator"
site:reddit.com/r/ "my agent" "subreddit"
site:reddit.com/r/ "AI moderator" "bot"
site:github.com "reddit" "autonomous agent"
```

Follow evidence into subreddit sidebars, pinned deployment announcements, bot profile disclosures, operator repositories, and dated comments. AutoModerator presence alone does not qualify. Treat AI-written-content allegations as allegations, not classifications.

## Discord: concrete destinations versus deployment leads

| Source / destination | Evidence level | Next useful action |
| --- | --- | --- |
| [SubSim interactive Discord](https://discord.gg/JxTU2ky) | Invite published by [the subreddit](https://www.reddit.com/r/SubSimGPT2Interactive/) | Best Reddit-to-Discord bridge. Invite acceptance, current channels and agents in Discord not yet inspected. |
| [OpenClaw Discord](https://discord.com/invite/clawd) | Invite followed from [official showcase](https://openclaw.ai/showcase) | Discover deployment/showcase channels and named agent fleets. Verified community link, not verified agent-managed server. |
| [Shapes documentation](https://docs.shapes.inc/introduction) and [Discord-related catalog](https://shapes.inc/tags/discord%20servers) | Platform/catalog for social AI; current homepage emphasizes a multiplayer assistant | Inspect current supported Discord routes and public deployments. Generated character lore is not deployment evidence; old Discord claims need current confirmation. |
| [Discord Agent Swarm](https://github.com/widingmarcus-cyber/discord-agent-swarm) | Maintainer documents named agents coordinating in Discord | Strong deployment lead; no public joinable server or observed live channel confirmed. |
| [Spacebot](https://github.com/spacedriveapp/spacebot) | First-party code/documentation for shared agent operation in Discord and other chat systems | Find explicitly published customer/community deployments. Capability is documented, deployment list is not. |
| [Cordbot](https://github.com/christianalfoni/cordbot) | Maintainer describes Claude-powered agents observing and helping Discord communities | Trace published demos and public servers; do not assume access to installations. |
| [Hivemoot](https://github.com/hivemoot) | Public named agent roster on GitHub; README mentions Discord | Cross-platform identity leads. Agents' GitHub operation does not establish their Discord operation; usable invite not confirmed. |
| [ComposioHQ](https://discord.com/servers/composiohq-1170785031560646836) | Discord's public listing describes official builder/support community | Human-builder source, not agent-run evidence. Seek disclosed deployed assistants. |
| [Botpress](https://discord.com/servers/botpress-1108396290624213082) | Public server listing for chatbot/automation builders | Deployment discovery; no claim that agents run the community. |
| [elizaOS community links](https://linktr.ee/elizaos) | Official community entry point; framework supports Discord agents | Trace public character deployments and bot demos, then verify specific channels. |

Do not label these ten leads “ten agent-run Discord servers.” The research confirms a mixture of public community links, deployment documentation, and promising sources. Live agent presence and public access remain per-server checks.

Search public Discord server listings, official project community links, GitHub READMEs, and bot catalogs. In an accessible server, examine published rules, bot profiles, app descriptions, demo/showcase channels and actual messages. Retain server ID, channel ID, bot/application ID and message permalink. A Discord bot/app badge establishes an application account, not AI capability. Do not infer cross-platform identity from matching names or avatars.

Useful searches:

```text
"Discord" "agents talking to each other"
"Discord" "AI agent" "public server"
"Discord" "agent swarm" "community"
site:github.com "discord" "agent" "demo"
site:discord.com/servers "AI agents"
site:reddit.com "Discord" "my agent"
```

## One shared identity model

Maintain distinct entities for **agent**, **operator**, **platform account**, **community**, and **deployment**. Link them only with explicit evidence. One agent can have several accounts, a community can host several agents, and a human operator can own many agents.

Minimum record:

```text
agent_id; display_name; category; operator_id (nullable)
platform; platform_account_id; current_handle; profile_url
community_id; channel_id; deployment_id (nullable)
source_family; source_url; source_record_id
identity_evidence_url; automation_evidence_url; activity_permalink
first_seen_at; last_checked_at; last_observed_activity_at
classification; evidence_state; access_state; interaction_route
darwin_fit; reason; rejection_reason; alias_history
```

Canonical IDs deduplicate accounts; evidence-backed relationships deduplicate agents. Store candidate records separately from confirmed agent records. Keep raw public observations and run manifests privately according to existing repository conventions. Ops remains responsible for CRM/PostHog; this research does not change current runtime ownership.

## Bounded collection plan

1. **Source pass:** enumerate the directory entries, existing local inventory when located, OpenClaw/Hermes deployment leads, Reddit bot roster, and public Discord links. Save all pagination/access outcomes. Produce candidates, not a fabricated agent total.
2. **X expansion:** run profile phrases, post queries, seed list-memberships, and bounded relationship expansion. Separate source families so crypto/social/assistant/coding coverage can be evaluated independently.
3. **Verification sample:** review 100 candidates across source families (or all if fewer). Read identity evidence and up to 20 recent posts/comments per account where available. Label human, company, ordinary bot, agent, hybrid or unresolved.
4. **Community pass:** inspect the two concrete Discord invites if accessible in the user's session; verify destination rules and actual agent channels. Record inaccessible/private destinations as such, without treating them as inactive.
5. **Expand high-yield sources:** paginate further where verified-agent yield is good; keep a smaller exploration allocation for new/non-English sources. Suggested first inventory milestone: 500 candidates and 100 documented agents, **targets rather than forecasts or existing counts**.
6. **Refresh design:** propose daily new-candidate discovery and weekly stale-record review only after the collector is implemented and scheduling requested. Nothing in this document starts monitoring.

For each source report discovered candidates, unique accounts, documented agents, observed-active agents, rejections, inaccessible records, pages completed, cost and incremental new-agent yield. Suggested saturation signal: two complete expansion rounds each add under 5% new documented agents within a specified source family/time window. This is a stopping heuristic, not proof of complete coverage. Never report a percentage of all X agents without a defensible denominator.

### API cost illustration

X's published pricing currently lists user reads at $0.010/resource, post reads at $0.005/resource, and follower/following reads at $0.010/resource. An illustrative 500 user records plus 20 posts each is **$55** before discovery/graph/list reads and other charges: $5 + $50. A 10,000-account follower harvest alone illustrates $100 in relationship reads. Confirm console rates and endpoint billing before execution. No credits were purchased or API requests billed in this research. [Current X pricing](https://docs.x.com/x-api/getting-started/pricing).

## Repository integration

The current [agent source inventory](discovery-sources.md) already covers OpenClaw and Hermes, but says its crawler primarily follows repositories rather than exhaustively enumerating social profiles. Extend discovery with explicit social-account/community record types instead of treating a repository as proof of an X agent. Keep the current Human Reply queries (workspace source: `CRM/X/config/ai-deck-searches.json`) as the relevance layer; add a separate identity-discovery query pack. The Reddit handoff (workspace source: `operation-agents/reddit-discovery.md`) currently qualifies human work evidence, so agent-authored records need their own classification rather than silently passing through as people.

Next implementation deliverables: source registry with access status, resumable read-only collectors, identity/evidence ledger, manual verification queue, and separate exports for X agents, agent-populated Reddit communities, and confirmed Discord deployments. Neither a runtime nor a verified large account export has been implemented by this research pass.
