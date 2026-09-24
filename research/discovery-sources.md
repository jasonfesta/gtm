# Agent Discovery — Source and Builder Inventory

Organized from the September 23, 2026 research. Evidence dates and qualification limits below remain those of the original research.

[Research index](README.md) · Sources: [agent-discovery-source-inventory.md](discovery-sources.md).

Captured from Jason's source list on September 23, 2026. These are user-supplied
research leads and descriptions, not independently verified facts, qualified
agents, verified owners, or permission to contact.

## Purpose and operating state

Retain sources for discovering actual agents and their associated builders.
Keep agent products, human builders, and supporting tools separate. A skill,
plugin, framework, directory, or deployment wrapper does not automatically qualify
as an agent. A named builder association requires source verification before an
owner link is saved. A social profile or repository is not a verified route for
messaging an agent.

This inventory does not enable sources, change qualification rules, restart the
stopped run, or authorize outreach. The executable registry remains
`config/agent-discovery-sources.json` (workspace source: `operation-agents/config/agent-discovery-sources.json`).
The current crawler primarily follows GitHub repositories; hosted products and
social-only builder leads are retained here for a future evidence path, without
silently bypassing the current activity requirements. CRM and PostHog calls remain
owned by Ops.

## OpenClaw and Hermes directories

All nine source families below are represented in the existing live registry,
but coverage is narrower than the descriptions: repository links are crawled;
forks, contributors, issues, social profiles, and every listed project are not
automatically enumerated. Hermes Atlas currently uses its `/ecosystem/` page.
Reddit access was blocked or returned an incomplete shell in the stopped pass.

| Source | URL | Supplied discovery value |
| --- | --- | --- |
| OpenClaw Showcase | https://openclaw.ai/showcase | Projects, skills, automations, and builders using OpenClaw. |
| ClawHub | https://clawhub.ai/ | Searchable directory of community-built OpenClaw skills. |
| OpenClaw GitHub | https://github.com/openclaw/openclaw | Core contributors and forks. |
| Hermes Atlas | https://hermesatlas.com/ | Community map tracking hundreds of Hermes repositories. |
| Hermes Atlas GitHub | https://github.com/ksimback/hermes-ecosystem | Underlying ecosystem index. |
| Awesome Hermes Agent | https://github.com/0xarkstar/awesome-hermes-agent | Curated Hermes tools, skills, deployments, and projects. |
| Hermes Showcase Thursday | https://www.reddit.com/r/hermesagent/comments/1w631p8/showcase_thursday_drop_your_hermes_projects_here/ | Recurring community launch thread. |
| Hermes Use Cases Megathread | https://www.reddit.com/r/hermesagent/comments/1t6gf4j/megathread_hermes_agent_use_cases_what_the/ | Community projects and workflows. |
| NousResearch/hermes-agent | https://github.com/NousResearch/hermes-agent | Official repository, forks, issues, and contributors. |

The existing registry also retains Hermes Skills Hub at
https://hermes-agent.nousresearch.com/docs/api/skills-index.json. It was already
configured and is not removed by this additional inventory.

## OpenClaw solo and indie builder leads

Names, solo/indie status, authorship, and product descriptions below are supplied
leads requiring verification. Preserve human and agent identities separately.

| Builder | Supplied source | Supplied project or association |
| --- | --- | --- |
| Peter Steinberger | https://x.com/steipete | Original OpenClaw creator. |
| Alex Thorp | https://launchclaw.app/about | LaunchClaw: simplified private OpenClaw deployment. |
| Will Prior | https://github.com/Flying-Pig-Labs/openclaw-public | OpenClaw Public: public version of his personal OpenClaw setup. |
| roboraw | https://github.com/roboraw/openclaw-quickstart | OpenClaw Quickstart: packaged OpenClaw, Ollama, and Docker setup. |
| l7-Holy | https://github.com/l7-Holy/openclaw-android-assistant | Android assistant connecting OpenClaw and Codex. |
| jdrhyne | https://x.com/jdrhyne | Multi-agent OpenClaw system and analytics skills. |
| Dan Peguine | https://x.com/danpeguine | Personal operating system with briefings, calendars, and invoices. |
| Dave Kiss | https://x.com/davekiss | Building and operating products through OpenClaw and Telegram. |
| Trebuh | https://x.com/iamtrebuh | Solo-founder setup using several specialized OpenClaw agents. |
| Aditya G | https://x.com/IamAdiG | Creator of Learn From Lenny, operated through WhatsApp. |
| Pedro Cruz | https://x.com/pepicrft | OpenClaw plugin for Ralph-style development projects. |
| MagiMetal | https://x.com/MagiMetal | macOS menu-bar manager for OpenClaw. |
| LLMJunky | https://x.com/LLMJunky | Gmail and calendar morning-rollup skill. |
| Buddy Hadry | https://x.com/buddyhadry | Alexa integration and command-line skill. |
| JJ P | https://x.com/jjpcodes | Agent-driven language-learning application. |
| Luka Radišić | https://x.com/LukaRadisic | OpenClaw integration for `ralph-tui`. |
| Christine Yip | https://x.com/christinetyip | Shared-memory skill for cooperating agents. |
| Quique Fagoaga | https://x.com/quifago | Real-estate search CLI and agent skill. |
| Swiftly Singh | https://x.com/swiftlysingh | Agent-generated Excalidraw diagrams. |

## Hermes solo and indie builder leads

| Builder | Supplied source | Supplied project or association |
| --- | --- | --- |
| Kevin Simback | https://github.com/ksimback/hermes-ecosystem | Hermes Atlas: searchable map of the Hermes ecosystem. |
| JPeetz | https://github.com/JPeetz/Hermes-Studio | Self-hosted Hermes dashboard with chat, skills, memory, terminal, and approvals. |
| Salomon Diei | https://github.com/Salomondiei08/oh-my-hermes | Oh My Hermes: application-building and operational workflow layer. |
| Nadia Ujovich | https://github.com/nujovich/hermes-mcp-lead-gen | Hermes MCP Lead Generation: autonomous lead research and website pre-auditing. |
| Snehal707 | https://github.com/Snehal707/Hermes-volta | Hermes Volta: natural-language analog circuit-design agent. |
| Aiman Malik | https://github.com/aimanmalib/apiforge | APIForge: multi-agent API scaffolding engine built with Hermes. |
| Lethe044 | https://github.com/Lethe044/hermes-incident-commander | Hermes Incident Commander: autonomous SRE and incident-response agent. |
| Nicolas Tinkl | https://github.com/nicolastinkl/hermes_weatherbot | Hermes Weatherbot: autonomous weather-prediction trading bot. |
| Vivek Shetye | https://github.com/vivekshetye/hermes-lead-generation-pipeline | Multi-agent B2B research and outreach system. |
| Brenon Araujo | https://github.com/brenonaraujo/git-meta-harness | Git Meta Harness: multi-agent development orchestration tested with Hermes. |
| Steve Kaplan | https://github.com/Stevekaplanai/alphaflow | AlphaFlow: options-flow Chrome extension powered partly by Hermes. |
| Alex Bogle | https://github.com/SaintChris/hermes-setup-showcase | Hermes Setup Showcase: prototype service for deploying personalized Hermes agents. |
| Maurice Mohr | https://github.com/mauricemohr88-debug/agent-trust-kit | Agent Trust Kit: trust, verification, and safety tooling for autonomous agents. |
| paraflu | https://github.com/paraflu/hermes-skill-xiaomi-miband10-vela-js | Xiaomi Mi Band Hermes Skill: wearable integration for Hermes. |
| rednicv | https://github.com/rednicv/redseek-rescue | RedSeek Rescue: bootable Windows repair system built using Hermes and DeepSeek. |

## Broader AI directories

Stored for future sourcing; these are not enabled adapters in the current registry.
The supplied comparative descriptions are research guidance, not verified rankings.

| Source | URL | Supplied discovery value |
| --- | --- | --- |
| Product Hunt — AI Agents | https://www.producthunt.com/categories/ai-agents | Launches, traction, comments, and discovering new products. |
| AI Agents Directory | https://aiagentsdirectory.com/ | Agent listings, categories, comparisons, open-source agents, and landscape maps. |
| There's An AI For That | https://theresanaiforthat.com/ | Broad task-based directory; search for an AI that can do a specific task. |
| Futurepedia | https://www.futurepedia.io/ | Curated and organized AI product directory. |
| FutureTools | https://www.futuretools.io/ | Editorially selected tools, rankings, newly added products, and newsletter. |
| Toolify | https://www.toolify.ai/ | Broad directory of products, estimated traffic rankings, GPTs, agents, and models; potentially noisy. |
| Altern | https://altern.ai/ | Searchable AI tools and agent categories. |
| AlternativeTo | https://alternativeto.net/ | Competitors and substitutes for a known product. |

## Early launches

Stored for future sourcing; not enabled adapters in the current registry.

| Source | URL | Supplied discovery value |
| --- | --- | --- |
| BetaList | https://betalist.com/ | Pre-launch and early-stage startups, including agents. |
| Uneed | https://www.uneed.best/ | Daily product launches with voting. |
| Hacker News — Show HN | https://news.ycombinator.com/show | Technical and open-source agents. |
| GitHub — AI Agents | https://github.com/topics/ai-agents | Open-source projects before they become products. |

## Developer platforms

Jason supplied this category heading but no entries yet. No platforms are inferred.

## Use on an explicitly resumed discovery pass

1. Use ecosystem directories and the named builder leads as source inputs; retain
   the exact listing-to-project-to-person evidence.
2. Keep broader directories and early launches as additional source families,
   with coverage and adapter work explicit rather than claiming they are live.
3. Deduplicate repository/product identities against saved candidates and through
   Ops against existing CRM cohorts before creating records.
4. Qualify actual agents separately from builders and infrastructure. Preserve
   unknown ownership, activity, permissions, and agent-operated routes as unknown.
5. Give Ops separate CRM and PostHog handoffs and require separate queried receipts.

See the canonical runbook (workspace source: `operation-agents/agent-discovery-agent.md`) and
saved leave-off state (workspace source: `operation-agents/agent-discovery-status.md`) for current rules and blockers.
