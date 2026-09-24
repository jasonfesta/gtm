import json
import tempfile
import unittest
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

from crm.human_discovery import (
    BATCH_TARGET,
    DAILY_TARGET,
    SEARCH_TERMS,
    SOURCES,
    build_handoffs,
    collect_live,
    combined_search_query,
    from_search_collection,
    from_search_collections,
    normalize_reddit,
    relevant_live_observation,
    requalify_handoff,
    write_handoffs,
)


class HumanDiscoveryTests(unittest.TestCase):
    def payload(self):
        return {
            "run_id": "manual-2026-09-22",
            "observed_at": "2026-09-22T12:00:00+00:00",
            "sources": {
                "x": [
                    {
                        "author": {"userName": "builder", "name": "Ada Builder"},
                        "id": "x1",
                        "url": "https://x.com/builder/status/x1",
                        "text": "I am building an AI assistant with MCP tool calling.",
                    }
                ],
                "reddit": [
                    {
                        "author": "researcher",
                        "id": "r1",
                        "permalink": "/r/LocalLLaMA/comments/r1/example/",
                        "title": "Research workflow",
                        "selftext": "Testing agentic search for analyst work.",
                    }
                ],
                "linkedin": [
                    {
                        "author": {
                            "name": "Ada Builder",
                            "publicIdentifier": "ada-builder",
                            "linkedinUrl": "https://www.linkedin.com/in/ada-builder/",
                        },
                        "id": "li1",
                        "linkedinUrl": "https://www.linkedin.com/feed/update/li1/",
                        "content": "Building an assistant for customer operations.",
                    }
                ],
                "hacker_news": [
                    {
                        "author": "hn-builder",
                        "id": "123",
                        "title": "Show HN: my coding agent",
                        "storyText": "I built a coding agent.",
                    }
                ],
                "product_hunt": [
                    {
                        "name": "Assistant Kit",
                        "url": "https://www.producthunt.com/posts/assistant-kit",
                        "tagline": "Tools for assistant developers",
                        "makers": [
                            {
                                "name": "Product Maker",
                                "username": "maker",
                                "url": "https://www.producthunt.com/@maker",
                            }
                        ],
                    }
                ],
                "apollo": [
                    {
                        "id": "a1",
                        "name": "Ada Builder",
                        "linkedin_url": "https://www.linkedin.com/in/ada-builder/",
                        "title": "AI assistant developer",
                    }
                ],
            },
            "cursors": {source: f"cursor-{source}" for source in SOURCES},
        }

    def test_reddit_normalization(self):
        item = normalize_reddit(
            {"author": "u/example", "id": "1", "permalink": "/r/test/comments/1/post"}
        )
        self.assertEqual(item["profile_url"], "https://www.reddit.com/user/example/")
        self.assertEqual(item["parent_url"], "https://www.reddit.com/r/test/comments/1/post")
        self.assertEqual(
            normalize_reddit({"authorUsername": "another"})["profile_url"],
            "https://www.reddit.com/user/another/",
        )
        self.assertEqual(normalize_reddit({"author": "[deleted]"})["profile_url"], "")

    def test_people_without_profile_identity_are_not_counted(self):
        payload = {
            "run_id": "anonymous",
            "sources": {
                "reddit": [
                    {
                        "author": "[deleted]",
                        "title": "Using Claude",
                        "id": "r1",
                    }
                ]
            },
        }
        crm, _ = build_handoffs(payload)
        self.assertEqual(crm["run_summary"]["unique_people"], 0)

    def test_combined_query_does_not_bias_audience_classification(self):
        payload = {
            "run_id": "solo",
            "sources": {
                "x": [
                    {
                        "channel": "x",
                        "name": "Solo Founder",
                        "profile_url": "https://x.com/solo_founder",
                        "text": "I am an independent founder building a small app.",
                        "query": combined_search_query(),
                    }
                ]
            },
        }
        crm, _ = build_handoffs(payload)
        self.assertEqual(crm["operations"][0]["payload"]["audience"], "solo developer")

    def test_live_relevance_rejects_casual_bot_mentions(self):
        self.assertFalse(
            relevant_live_observation(
                {
                    "channel": "x",
                    "text": "@grok is he lying?",
                }
            )
        )
        self.assertFalse(
            relevant_live_observation(
                {
                    "channel": "x",
                    "text": "Devin G. likes elevator music",
                }
            )
        )
        self.assertFalse(
            relevant_live_observation(
                {
                    "channel": "x",
                    "name": "Hermes Release Watch",
                    "text": "Hermes Agent project update",
                }
            )
        )
        self.assertFalse(
            relevant_live_observation(
                {
                    "channel": "x",
                    "name": "Visual Artist",
                    "text": "The cursor effect is part of my project design",
                }
            )
        )
        self.assertTrue(
            relevant_live_observation(
                {
                    "channel": "x",
                    "text": "I'm building my app with Cursor",
                }
            )
        )
        self.assertTrue(
            relevant_live_observation(
                {
                    "channel": "x",
                    "text": "This is how I use ChatGPT",
                }
            )
        )

    def test_held_live_handoff_can_be_requalified_without_provider_rerun(self):
        old = {
            "run_id": "first-pass",
            "observed_at": "2026-09-22T16:00:00+00:00",
            "operations": [
                {
                    "payload": {
                        "name": "Builder",
                        "profile_url": "https://x.com/builder",
                        "sources": [
                            {
                                "source": "x",
                                "profile_url": "https://x.com/builder",
                                "text": "I'm building an app with Cursor",
                            }
                        ],
                    }
                },
                {
                    "payload": {
                        "name": "Casual",
                        "profile_url": "https://x.com/casual",
                        "sources": [
                            {
                                "source": "x",
                                "profile_url": "https://x.com/casual",
                                "text": "@grok is he lying?",
                            }
                        ],
                    }
                },
            ],
        }
        payload = requalify_handoff(
            old,
            {"query": combined_search_query(), "provider_runs": [{"source": "x", "returned": 2}]},
        )
        crm, _ = build_handoffs(payload)
        self.assertEqual(payload["run_id"], "first-pass-qualified")
        self.assertEqual(crm["run_summary"]["unique_people"], 1)

    def test_six_sources_and_cross_source_identity_dedup(self):
        crm, posthog = build_handoffs(self.payload())
        self.assertEqual(crm["run_summary"]["observations"], 6)
        self.assertEqual(crm["run_summary"]["unique_people"], 5)
        self.assertEqual(crm["run_summary"]["target_per_day"], DAILY_TARGET)
        self.assertEqual(crm["run_summary"]["target_per_four_hour_batch"], BATCH_TARGET)
        self.assertFalse(crm["run_summary"]["batch_target_met"])
        self.assertEqual(set(crm["run_summary"]["by_source"]), set(SOURCES))
        records = [row["payload"] for row in crm["operations"]]
        ada = [row for row in records if row["name"] == "Ada Builder"]
        self.assertEqual(len(ada), 2)
        linkedin = [row for row in ada if row["profile_url"].startswith("https://www.linkedin")]
        self.assertEqual(len(linkedin), 1)
        self.assertEqual({s["source"] for s in linkedin[0]["sources"]}, {"linkedin", "apollo"})
        self.assertEqual(crm["cursors"]["reddit"], "cursor-reddit")
        self.assertEqual(posthog["source_agent"], "human discovery agent")
        self.assertEqual(posthog["schema_version"], 1)
        event = posthog["events"][0]
        self.assertEqual(event["event"], "gtm.human_discovery_run")
        self.assertEqual(event["properties"]["distinct_id"], "human-discovery-agent")
        self.assertFalse(event["properties"]["$process_person_profile"])
        for key, value in crm["run_summary"].items():
            self.assertEqual(event["properties"][key], value)

    def test_hacker_news_authors_remain_distinct(self):
        payload = self.payload()
        payload["sources"]["hacker_news"].append(
            {
                "author": "another-builder",
                "id": "456",
                "title": "Show HN: another agent",
                "storyText": "I built another coding agent.",
            }
        )
        crm, _ = build_handoffs(payload)
        records = [row["payload"] for row in crm["operations"]]
        hn = [row for row in records if "news.ycombinator.com/user?id=" in row["profile_url"]]
        self.assertEqual(len(hn), 2)

    def test_replay_writes_same_handoff_content(self):
        with tempfile.TemporaryDirectory() as temp:
            first = write_handoffs(self.payload(), temp)
            before = {key: Path(path).read_text() for key, path in first.items()}
            second = write_handoffs(self.payload(), temp)
            after = {key: Path(path).read_text() for key, path in second.items()}
            self.assertEqual(before, after)
            self.assertEqual(json.loads(after["crm"])["run_id"], "manual-2026-09-22")

    def test_existing_search_collection_becomes_discovery_input(self):
        collection = {
            "hour": "2026-09-22T11",
            "window": {"end": "2026-09-22T12:00:00+00:00"},
            "items": [
                {
                    "channel": "x",
                    "name": "Search Builder",
                    "profile_url": "https://x.com/search_builder",
                    "parent_url": "https://x.com/search_builder/status/1",
                    "provider_id": "1",
                    "text": "Building an AI assistant.",
                },
                {
                    "channel": "github",
                    "name": "ignored-project-source",
                    "profile_url": "https://github.com/example",
                },
            ],
        }
        payload = from_search_collection(
            collection,
            reddit={
                "cursor": "reddit-next",
                "items": [
                    {
                        "author": "reddit-builder",
                        "id": "r2",
                        "permalink": "/r/agents/comments/r2/example/",
                        "title": "Agent workflow",
                    }
                ],
            },
        )
        crm, posthog = build_handoffs(payload)
        self.assertEqual(crm["run_summary"]["unique_people"], 2)
        self.assertEqual(crm["cursors"]["reddit"], "reddit-next")
        self.assertEqual(
            posthog["events"][0]["properties"]["unique_people"],
            crm["run_summary"]["unique_people"],
        )

    def test_four_hour_keyword_batch_combines_search_collections(self):
        collections = []
        for hour in range(8, 12):
            collections.append(
                {
                    "hour": f"2026-09-22T{hour:02d}",
                    "window": {"end": f"2026-09-22T{hour + 1:02d}:00:00+00:00"},
                    "items": [
                        {
                            "channel": "x",
                            "name": f"Builder {hour}",
                            "profile_url": f"https://x.com/builder_{hour}",
                            "parent_url": f"https://x.com/builder_{hour}/status/1",
                            "provider_id": str(hour),
                            "text": "Building an AI assistant.",
                            "query": "AI assistant developer",
                        }
                    ],
                }
            )
        payload = from_search_collections(collections)
        crm, _ = build_handoffs(payload)
        self.assertEqual(crm["run_summary"]["observations"], 4)
        self.assertEqual(crm["run_summary"]["unique_people"], 4)
        self.assertEqual(crm["cursors"]["x"], "2026-09-22T12:00:00+00:00")

    def test_posthog_event_uuid_is_stable_for_replay(self):
        _, first = build_handoffs(self.payload())
        _, second = build_handoffs(self.payload())
        self.assertEqual(first["events"][0]["uuid"], second["events"][0]["uuid"])

    def test_daily_target_is_two_hundred_and_batch_target_is_thirty_four(self):
        self.assertEqual(DAILY_TARGET, 200)
        self.assertEqual(BATCH_TARGET, 34)

    def test_sidebar_terms_are_one_combined_query(self):
        self.assertEqual(len(SEARCH_TERMS), 25)
        self.assertEqual(len(set(SEARCH_TERMS)), 25)
        query = combined_search_query()
        self.assertTrue(query.startswith("(") and query.endswith(")"))
        self.assertEqual(query.count(" OR "), 24)
        for term in SEARCH_TERMS:
            self.assertEqual(query.count(json.dumps(term)), 1)

    def test_manual_collection_uses_source_specific_queries(self):
        calls = []
        rows = {
            "twitter-x-data": [
                {
                    "author": {"userName": "x_builder", "name": "X Builder"},
                    "id": "x1",
                    "text": "Building with ChatGPT",
                    "url": "https://x.com/x_builder/status/x1",
                }
            ],
            "reddit-scraper": [
                {
                    "author": "reddit_builder",
                    "id": "r1",
                    "title": "Using Claude in my research workflow",
                    "permalink": "/r/agents/comments/r1/example/",
                }
            ],
            "linkedin-post-search": [
                {
                    "author": {"name": "LinkedIn Builder", "publicIdentifier": "li-builder"},
                    "id": "li1",
                    "content": "Using Cursor in my coding workflow",
                }
            ],
            "hacker-news-scraper": [
                {
                    "author": "hn-builder",
                    "id": "123",
                    "title": "Show HN: Grok tool",
                }
            ],
            "producthunt-daily-scraper": [
                {
                    "name": "OpenClaw plugin",
                    "url": "https://www.producthunt.com/posts/plugin",
                    "makers": [{"name": "PH Builder", "username": "ph-builder"}],
                },
                {
                    "name": "Unrelated product",
                    "url": "https://www.producthunt.com/posts/other",
                    "makers": [{"name": "Other Maker", "username": "other"}],
                },
            ],
        }

        def fake_apify(actor, payload, *, auth):
            calls.append((actor, payload, auth))
            key = next(key for key in rows if key in actor)
            return rows[key], f"run-{key}"

        payload = collect_live(
            now=datetime(2026, 9, 22, 16, tzinfo=timezone.utc),
            apify_call=fake_apify,
            apify_auth="test-apify",
            terms=["Claude"],
        )
        self.assertEqual(len(calls), 4)
        self.assertEqual(len(payload["provider_runs"]), 4)
        self.assertEqual(len(payload["sources"]["apollo"]), 0)
        self.assertEqual(len(payload["sources"]["linkedin"]), 0)
        reddit = next(p for a, p, _ in calls if "reddit" in a)
        self.assertEqual(reddit["searches"], ["Claude"])
        hn = next(p for a, p, _ in calls if "hacker-news" in a)
        self.assertEqual(hn["query"], "Claude")
        with tempfile.TemporaryDirectory() as temp:
            paths = write_handoffs(payload, temp)
            self.assertEqual(set(paths), {"crm", "posthog", "collection"})
            self.assertTrue(all(Path(path).is_absolute() for path in paths.values()))
            self.assertEqual(
                json.loads(Path(paths["crm"]).read_text())["run_summary"]["unique_people"], 4
            )
            receipt = json.loads(Path(paths["collection"]).read_text())
            self.assertEqual(receipt["daily_unique_people"], 4)
            self.assertEqual(receipt["report_day"], "2026-09-22")

            repeat = dict(payload)
            repeat["run_id"] = "human-discovery-second-batch"
            repeat["sources"] = {
                source: list(items) for source, items in payload["sources"].items()
            }
            repeat["sources"]["x"].append(
                {
                    "channel": "x",
                    "name": "New Builder",
                    "profile_url": "https://x.com/new_builder",
                    "query": combined_search_query(),
                }
            )
            repeat_paths = write_handoffs(repeat, temp)
            repeat_receipt = json.loads(Path(repeat_paths["collection"]).read_text())
            self.assertEqual(repeat_receipt["daily_unique_people"], 5)

    def test_sales_navigator_and_apollo_do_not_launch_generic_searches(self):
        def forbidden(*args, **kwargs):
            raise AssertionError("no provider call allowed")

        payload = collect_live(
            selected_sources=["linkedin", "apollo"], apify_auth="test", apify_call=forbidden
        )
        self.assertEqual(
            [r["error"] for r in payload["provider_runs"]],
            ["sales_navigator_manual_handoff_required", "validation_only_not_a_discovery_source"],
        )

    def test_manual_collection_reports_missing_credentials_without_fake_people(self):
        payload = collect_live(
            now=datetime(2026, 9, 22, 16, tzinfo=timezone.utc),
            apify_auth="",
        )
        self.assertTrue(all(not rows for rows in payload["sources"].values()))
        self.assertEqual(len(payload["provider_runs"]), 4)
        self.assertTrue(all("error" in row for row in payload["provider_runs"]))

    def test_manual_collection_records_provider_network_error(self):
        def offline(*args, **kwargs):
            raise urllib.error.URLError("offline")

        payload = collect_live(
            now=datetime(2026, 9, 22, 16, tzinfo=timezone.utc),
            apify_call=offline,
            apify_auth="test-apify",
        )
        self.assertEqual(len(payload["provider_runs"]), 8)
        self.assertTrue(all("error" in row for row in payload["provider_runs"]))


if __name__ == "__main__":
    unittest.main()
