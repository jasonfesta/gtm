import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from gtm_agent_runtime.agent_discovery import (
    normalize_source_record,
    process,
    run,
    verify_export_activity,
)
from gtm_agent_runtime.directory_crawler import Client, commit, crawl, source_repositories


def classifier(record):
    return record["decision"]


class FakeDirectoryClient:
    def readme(self, slug):
        return "https://github.com/alice/research-fox\nhttps://github.com/bob/browser-bear\n"

    def text(self, url):
        pages = {
            "https://clawhub.ai/": (
                '<a href="/alice/skills/research-fox">Research Fox</a>'
                '<a href="/bob/plugins/browser-bear">Browser Bear</a>'
            ),
            "https://clawhub.ai/alice/skills/research-fox": (
                '<a href="https://github.com/alice/research-fox">Repository</a>'
            ),
            "https://clawhub.ai/bob/plugins/browser-bear": (
                '<a href="https://github.com/bob/browser-bear">Repository</a>'
            ),
        }
        return pages[url]

    def repo(self, slug):
        return {
            "name": slug.split("/")[1],
            "description": "Active personal agent",
            "html_url": "https://github.com/" + slug,
            "homepage": "",
            "pushed_at": "2026-09-20T00:00:00Z",
            "stargazers_count": 100,
            "forks_count": 10,
            "archived": False,
            "disabled": False,
        }

    def latest_commit(self, slug):
        return {
            "html_url": "https://github.com/" + slug + "/commit/1",
            "author": {
                "login": "active-builder",
                "html_url": "https://github.com/active-builder",
            },
            "commit": {"author": {"date": "2026-09-20T00:00:00Z"}},
        }


class AgentDiscoveryTests(unittest.TestCase):
    def test_portkey_excludes_private_contact_and_crm_fields(self):
        from gtm_agent_runtime.portkey import public_record

        record = {
            "index_id": "public",
            "description": "Public project",
            "email": "private",
            "contacts": [{"email": "private"}],
            "crm_snapshot": {"private": True},
            "owner_developer_evidence": {"github_login": "public-author", "email": "private"},
        }
        payload = public_record(record)
        self.assertNotIn("private", json.dumps(payload))
        self.assertEqual(payload["owner_developer_evidence"], {"github_login": "public-author"})

    def test_handoff_contract_and_contributor_hold(self):
        from gtm_agent_runtime.agent_discovery import crm_operations
        from gtm_agent_runtime.posthog import read_handoff

        record = {
            "index_id": "agent",
            "name": "Agent",
            "source_url": "https://example.com",
            "decision": {
                "kind": "agent",
                "audience": "personal_agents",
                "capabilities": {"can_receive_requests": True, "invented": True},
                "owner_developer": {
                    "name": "Contributor",
                    "relationship": "recent_repository_contributor",
                    "source_url": "https://github.com/a/b/commit/1",
                    "evidence": "commit",
                },
            },
        }
        changes, review = process([record], [], classifier)
        self.assertFalse(review)
        operations = crm_operations(changes)
        self.assertEqual(
            operations[1]["payload"]["capabilities"],
            {"can_receive_requests": "yes"},
        )
        self.assertNotIn("agent_owner_link", [op["action"] for op in operations])
        with tempfile.TemporaryDirectory() as folder:
            result = run({"state_dir": folder}, records=[record], classifier=classifier)
            self.assertEqual(result["human_links"], 0)
            self.assertEqual(result["owner_link_holds"], 1)
            events = read_handoff(Path(result["posthog_handoff"]))
            self.assertEqual(events[0]["properties"]["qualified_agents"], 1)
            self.assertEqual(len(events), 1)

    def test_github_token_is_not_sent_to_external_directories(self):
        from io import BytesIO

        requests = []

        def opener(request, **kwargs):
            requests.append(request)
            return BytesIO(b"{}")

        client = Client(opener=opener)
        client.token = "test-only-token"
        for url in (
            "https://api.github.com/repos/a/b",
            "https://directory.example/index",
            "https://api.github.com.evil.example/index",
            "http://api.github.com/repos/a/b",
        ):
            client.json(url)
        self.assertEqual(requests[0].get_header("Authorization"), "Bearer test-only-token")
        self.assertNotIn("Authorization", requests[0].headers)
        self.assertTrue(
            all(request.get_header("Authorization") is None for request in requests[1:])
        )

    def test_http_budget_stops_before_another_network_request(self):
        from io import BytesIO

        calls = []

        def opener(request, **kwargs):
            calls.append(request.full_url)
            return BytesIO(b"{}")

        client = Client(opener=opener, request_limit=1)
        client.json("https://directory.example/first")
        with self.assertRaisesRegex(RuntimeError, "budget exhausted"):
            client.json("https://directory.example/second")
        self.assertEqual(len(calls), 1)

    def test_recent_commit_skips_bot_and_finds_developer(self):
        client = Client()
        client.json = lambda url: [
            {"author": {"login": "dependabot[bot]"}},
            {"author": {"login": "active-builder"}},
        ]
        self.assertEqual(
            client.latest_commit("alice/research-fox")["author"]["login"],
            "active-builder",
        )

    def test_clawhub_crawls_detail_pages_for_repositories(self):
        repositories = source_repositories(
            {
                "id": "clawhub",
                "kind": "clawhub",
                "url": "https://clawhub.ai/",
            },
            FakeDirectoryClient(),
        )
        self.assertEqual(
            repositories,
            ["alice/research-fox", "bob/browser-bear"],
        )

    def test_github_readme_extracts_listed_projects(self):
        repositories = source_repositories(
            {
                "kind": "github_readme",
                "url": "https://github.com/0xarkstar/awesome-hermes-agent",
            },
            FakeDirectoryClient(),
        )
        self.assertEqual(repositories, ["alice/research-fox", "bob/browser-bear"])

    def test_directory_crawl_selects_active_once(self):
        config = {
            "daily_target": 250,
            "total_target": 600,
            "active_project_days": 30,
            "active_developer_days": 30,
            "sources": [
                {
                    "id": "openclaw_github",
                    "kind": "github_repo",
                    "url": "https://github.com/openclaw/openclaw",
                }
            ],
        }
        with tempfile.TemporaryDirectory() as folder:
            checkpoint = Path(folder) / "checkpoint.json"
            selected, sources, state = crawl(
                config,
                checkpoint,
                client=FakeDirectoryClient(),
                as_of=datetime(2026, 9, 22, tzinfo=timezone.utc),
            )
            self.assertEqual(len(selected), 1)
            self.assertEqual(selected[0]["index_id"], "github:openclaw/openclaw")
            self.assertEqual(sources[0]["selected"], 1)
            self.assertTrue(checkpoint.exists())
            commit(
                checkpoint,
                state,
                processed_ids=[selected[0]["index_id"]],
                qualified_ids=[selected[0]["index_id"]],
            )
            repeated, _, state = crawl(
                config,
                checkpoint,
                client=FakeDirectoryClient(),
                as_of=datetime(2026, 9, 22, tzinfo=timezone.utc),
            )
            self.assertFalse(repeated)
            self.assertEqual(state["_total_selected"], 1)

    def test_unprocessed_ranked_candidate_remains_for_next_run(self):
        config = {
            "daily_target": 1,
            "total_target": 2,
            "max_candidates_per_run": 1,
            "max_repository_checks": 2,
            "sources": [
                {
                    "id": "awesome_hermes_agent",
                    "kind": "github_readme",
                    "url": "https://github.com/0xarkstar/awesome-hermes-agent",
                }
            ],
        }
        with tempfile.TemporaryDirectory() as folder:
            checkpoint = Path(folder) / "checkpoint.json"
            first, _, state = crawl(
                config,
                checkpoint,
                client=FakeDirectoryClient(),
                as_of=datetime(2026, 9, 22, tzinfo=timezone.utc),
            )
            self.assertEqual(len(first), 1)
            self.assertEqual(len(state["_pending"]), 2)
            commit(
                checkpoint,
                state,
                processed_ids=[first[0]["index_id"]],
                qualified_ids=[first[0]["index_id"]],
            )
            second, _, state = crawl(
                config,
                checkpoint,
                client=FakeDirectoryClient(),
                as_of=datetime(2026, 9, 22, tzinfo=timezone.utc),
            )
            self.assertEqual(len(second), 1)
            self.assertNotEqual(first[0]["index_id"], second[0]["index_id"])

    def test_failed_source_does_not_write_checkpoint(self):
        class FailingClient(FakeDirectoryClient):
            def repo(self, slug):
                raise RuntimeError("GitHub unavailable")

        config = {
            "sources": [
                {
                    "id": "openclaw_github",
                    "kind": "github_repo",
                    "url": "https://github.com/openclaw/openclaw",
                }
            ]
        }
        with tempfile.TemporaryDirectory() as folder:
            checkpoint = Path(folder) / "checkpoint.json"
            with self.assertRaisesRegex(RuntimeError, "no candidates"):
                crawl(
                    config,
                    checkpoint,
                    client=FailingClient(),
                    as_of=datetime(2026, 9, 22, tzinfo=timezone.utc),
                )
            self.assertFalse(checkpoint.exists())

    def test_interrupted_acquisition_resumes_after_last_saved_repository(self):
        calls = []

        class InterruptedClient(FakeDirectoryClient):
            def repo(self, slug):
                calls.append(slug)
                if slug.startswith("bob/"):
                    raise KeyboardInterrupt()
                return super().repo(slug)

        config = {
            "sources": [
                {
                    "id": "directory",
                    "kind": "github_readme",
                    "url": "https://github.com/example/directory",
                }
            ]
        }
        with tempfile.TemporaryDirectory() as folder:
            checkpoint = Path(folder) / "checkpoint.json"
            with self.assertRaises(KeyboardInterrupt):
                crawl(
                    config,
                    checkpoint,
                    client=InterruptedClient(),
                    as_of=datetime(2026, 9, 22, tzinfo=timezone.utc),
                )
            saved = json.loads(checkpoint.read_text())
            self.assertEqual(saved["directory"]["offset"], 1)
            self.assertEqual(len(saved["_pending"]), 1)
            self.assertFalse(saved.get("_processed"))
            selected, sources, _ = crawl(
                config,
                checkpoint,
                client=FakeDirectoryClient(),
                as_of=datetime(2026, 9, 22, tzinfo=timezone.utc),
            )
            self.assertEqual(len(selected), 2)
            self.assertEqual(sources[0]["inspected"], 1)

    def test_repository_budget_is_global_and_next_pass_rotates_sources(self):
        config = {
            "max_repository_checks": 1,
            "sources": [
                {"id": "first", "kind": "github_repo", "url": "https://github.com/alice/one"},
                {"id": "second", "kind": "github_repo", "url": "https://github.com/bob/two"},
            ],
        }
        with tempfile.TemporaryDirectory() as folder:
            checkpoint = Path(folder) / "checkpoint.json"
            first, sources, _ = crawl(
                config,
                checkpoint,
                client=FakeDirectoryClient(),
                as_of=datetime(2026, 9, 22, tzinfo=timezone.utc),
            )
            self.assertEqual(len(first), 1)
            self.assertEqual(sum(row["inspected"] for row in sources), 1)
            second, sources, _ = crawl(
                config,
                checkpoint,
                client=FakeDirectoryClient(),
                as_of=datetime(2026, 9, 22, tzinfo=timezone.utc),
            )
            self.assertEqual(len(second), 2)
            self.assertEqual(sources[0]["source_id"], "second")

    def test_non_agent_does_not_consume_agent_cap_or_crm_handoff(self):
        with tempfile.TemporaryDirectory() as folder:
            result = run(
                {"state_dir": folder},
                records=[
                    {
                        "index_id": "skill-1",
                        "name": "Skill package",
                        "decision": {"kind": "irrelevant"},
                    },
                    {
                        "index_id": "agent-1",
                        "name": "Research Fox",
                        "decision": {
                            "kind": "agent",
                            "audience": "research_agents",
                        },
                    },
                    {
                        "index_id": "agent-2",
                        "name": "Browser Bear",
                        "decision": {
                            "kind": "agent",
                            "audience": "assistant_agents",
                        },
                    },
                ],
                agent_limit=1,
            )
            handoff = json.loads(Path(result["crm_handoff"]).read_text())
            self.assertEqual(result["records_read"], 2)
            self.assertEqual(result["qualified_agents"], 1)
            self.assertEqual(result["_processed_ids"], ["skill-1", "agent-1"])
            self.assertEqual(result["_qualified_ids"], ["agent-1"])
            self.assertEqual(
                [op["action"] for op in handoff["operations"]],
                ["agent_upsert", "relationship_classify"],
            )

    def test_maps_hermes_index_fields_without_promoting_contributor(self):
        record = normalize_source_record(
            {
                "skill_identifier": "official/security/1password",
                "agent_name": "hermes-agent",
                "skill_description": "Uses the 1Password skill.",
                "repo_url": "https://github.com/NousResearch/hermes-agent",
                "author_basis": "recent_repository_contributor",
                "author_profile": {"login": "example-contributor"},
            },
            {
                "source": "https://hermes.example/skills-index.json",
                "frozen_at": "2026-09-22T16:17:57Z",
            },
        )
        self.assertEqual(record["index_id"], "official/security/1password")
        self.assertEqual(record["source_name"], "hermes_skills")
        self.assertEqual(record["name"], "hermes-agent")
        self.assertEqual(
            record["repository_url"],
            "https://github.com/NousResearch/hermes-agent",
        )
        self.assertNotIn("owner_developer", record)
        self.assertNotIn("updated_at", record)

    def test_export_activity_keeps_only_recent_project_and_developer(self):
        rows = [
            {
                "index_id": "recent",
                "repository_url": "https://github.com/alice/research-fox",
                "updated_at": "2026-09-20T00:00:00Z",
            },
            {
                "index_id": "old",
                "repository_url": "https://github.com/bob/browser-bear",
                "updated_at": "2026-07-20T00:00:00Z",
            },
        ]
        active = verify_export_activity(
            rows,
            FakeDirectoryClient(),
            as_of=datetime(2026, 9, 22, tzinfo=timezone.utc),
        )
        self.assertEqual([row["index_id"] for row in active], ["recent"])
        self.assertEqual(
            active[0]["developer_activity_evidence"]["github_login"],
            "active-builder",
        )

    def test_creates_crm_handoff_and_summary(self):
        records = [
            {
                "index_id": "idx-1",
                "name": "Research Fox",
                "description": "Researches technical questions",
                "website_url": "https://fox.example",
                "source_url": "https://index.example/idx-1",
                "decision": {
                    "kind": "agent",
                    "audience": "research_agents",
                    "darwin_fit": "Can use Darwin for research.",
                    "capabilities": {"can_receive_requests": "yes"},
                    "routes": [{"channel": "agent_email", "address": "fox@example.com"}],
                    "owner_developer": {
                        "name": "A. Builder",
                        "source_url": "https://fox.example/about",
                    },
                },
            }
        ]
        with tempfile.TemporaryDirectory() as folder:
            result = run(
                {"state_dir": folder, "fixture_next_cursor": "cursor-2"},
                records=records,
                classifier=classifier,
            )
            handoff = json.loads(Path(result["crm_handoff"]).read_text())
            self.assertEqual(handoff["source_agent"], "agent discovery agent")
            self.assertEqual(handoff["operations"][0]["action"], "agent_upsert")
            self.assertEqual(handoff["operations"][1]["payload"]["audience"], "research_agents")
            self.assertEqual(
                handoff["operations"][0]["payload"]["evidence"]["index_id"],
                "idx-1",
            )
            self.assertEqual(result["qualified_agents"], 1)
            self.assertEqual(result["reachable_agents"], 0)
            posthog = json.loads(Path(result["posthog_handoff"]).read_text())
            self.assertEqual(posthog["events"][0]["event"], "agent_discovery_completed")
            self.assertEqual(posthog["events"][0]["properties"]["qualified_agents"], 1)
            cursor = json.loads((Path(folder) / "cursor.json").read_text())
            self.assertEqual(cursor["cursor"], "cursor-2")

    def test_matches_existing_agent(self):
        record = {
            "index_id": "idx-2",
            "name": "Helper",
            "website_url": "https://helper.example/",
            "decision": {
                "kind": "agent",
                "audience": "assistant_agents",
                "darwin_fit": "Useful assistant.",
                "capabilities": {},
                "routes": [],
                "owner_developer": None,
            },
        }
        changes, review = process(
            [record],
            [
                {
                    "entity_id": "agt-existing",
                    "canonical_name": "Helper",
                    "website_url": "https://helper.example",
                }
            ],
            classifier,
        )
        self.assertFalse(review)
        self.assertEqual(changes[0]["action"], "update")
        self.assertEqual(changes[0]["agent_id"], "agt-existing")

    def test_duplicate_index_record_does_not_duplicate_output(self):
        record = {
            "index_id": "idx-3",
            "name": "Personal Runner",
            "decision": {
                "kind": "agent",
                "audience": "personal_agents",
                "darwin_fit": "Runs personal workflows.",
                "capabilities": {},
                "routes": [],
                "owner_developer": None,
            },
        }
        changes, review = process([record, record], [], classifier)
        self.assertEqual(len(changes), 1)
        self.assertFalse(review)

    def test_unclear_record_goes_to_review(self):
        changes, review = process([{"name": "Unknown"}], [], classifier)
        self.assertFalse(changes)
        self.assertEqual(review[0]["reason"], "missing index ID")

    def test_malformed_model_route_goes_to_review(self):
        changes, review = process(
            [
                {
                    "index_id": "agent-1",
                    "decision": {
                        "kind": "agent",
                        "audience": "assistant_agents",
                        "routes": "agent@example.com",
                    },
                }
            ],
            [],
            classifier,
        )
        self.assertFalse(changes)
        self.assertEqual(review[0]["reason"], "routes must be a list of objects")

    def test_private_route_and_owner_policy_require_explicit_evidence(self):
        decisions = [
            {
                "kind": "agent",
                "audience": "assistant_agents",
                "routes": [
                    {
                        "channel": "email",
                        "address": "team@example.com",
                        "agent_operated": True,
                    },
                    {
                        "channel": "email",
                        "address": "agent@example.com",
                        "agent_operated": True,
                        "agent_operated_evidence": "Agent inbox",
                        "agent_operated_source_url": "https://example.com/contact",
                        "verified_at": "2026-01-01T00:00:00Z",
                    },
                ],
                "owner_approval_required": False,
            }
        ]
        record = {"index_id": "agent-route", "name": "Agent Route"}
        changes, review = process([record], [], lambda _: decisions[0])
        self.assertFalse(review)
        self.assertIsNone(changes[0]["routes"][0]["agent_operated"])
        self.assertTrue(changes[0]["routes"][1]["agent_operated"])
        self.assertEqual(changes[0]["owner_approval_required"], "unknown")


if __name__ == "__main__":
    unittest.main()
