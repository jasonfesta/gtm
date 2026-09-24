# Separate Human Reply Agent tasks

Use [HUMAN_REPLY_STARTUP.md](startup.md) to start one platform in its
own task. [rules.md](rules.md) is the shared contract; read only the selected
platform reference alongside it:

- X: the X loop in rules.md.
- Reddit: [human-reply-reddit.md](reddit.md).
- Hacker News: [human-reply-hacker-news.md](hacker-news.md).
- LinkedIn: [human-reply-linkedin.md](linkedin.md).

This replaces the former hourly side-task instructions. Do not use the old
runtime registry to start schedules or gate an explicitly requested manual run.
