# Start a separate Human Reply Agent

In a separate task for each platform, use one of these prompts:

- `Start human reply agent X`
- `Start human reply agent Reddit`
- `Start human reply agent Hacker News`
- `Start human reply agent LinkedIn`

The startup skill selects only that platform and renames the current task to
`Human Reply Agent X`, `Human Reply Agent Reddit`, `Human Reply Agent Hacker News`,
or `Human Reply Agent LinkedIn`. An unqualified new start asks for the platform.
X captures up to 100 posts across loaded X Pro columns, works through up to
25 worthwhile replies, then reports and stops. These are upper limits, not
minimum targets; take as long as needed to finish the selected queue.
Reddit, LinkedIn and Hacker News each search 10 keywords through Apify, aim for
at least 25 suitable candidate permalinks, spread selected replies across about
an hour, finish their queue as long as needed, then report and end. There is no
minimum number of posts; weak results are skipped. Each task has independent platform state and daily records;
each fresh start creates a new browser tab and a unique run-scoped lease,
allowing independent browser work across platform tasks.

[rules.md](rules.md) is the shared contract. The skill loads the selected
platform reference. Reddit, LinkedIn and Hacker News use
[Apify discovery](apify.md), collect permalinks, then open those
exact targets in their dedicated browsers to draft, post and verify replies.
No direct-site searching or links-only HN handoff. X keeps X Pro discovery.
No schedules, side tasks, Portkey or combined five-tab runs are part of startup.
