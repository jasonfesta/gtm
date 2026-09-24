import unittest

from crm.assistant_fit import score_post, score_post_via_portkey

BUILDER = (
    "we shipped our voice agent last night and wired an mcp server so the "
    "tool loop keeps agent memory across retries. the eval harness caught a broken handoff."
)


def item(channel="x", text=BUILDER, **extra):
    profiles = {
        "x": ("https://x.com/builder", "https://x.com/builder/status/1"),
        "linkedin": (
            "https://www.linkedin.com/in/builder",
            "https://www.linkedin.com/feed/update/urn:li:activity:1",
        ),
        "hacker_news": (
            "https://news.ycombinator.com/user?id=builder",
            "https://news.ycombinator.com/item?id=1",
        ),
        "github": ("https://github.com/builder", "https://github.com/builder/agent"),
        "product_hunt": (
            "https://www.producthunt.com/@builder",
            "https://www.producthunt.com/posts/agent",
        ),
        "apollo": (
            "https://www.linkedin.com/in/builder",
            "https://www.linkedin.com/in/builder",
        ),
    }
    profile, parent = profiles[channel]
    row = {
        "channel": channel,
        "name": "Builder",
        "profile_url": profile,
        "parent_url": parent,
        "text": text,
    }
    row.update(extra)
    return row


class AssistantFitTests(unittest.TestCase):
    def test_live_score_uses_verified_portkey_result(self):
        def complete(prompt, max_completion_tokens):
            self.assertIn("tool-discovery outreach watcher", prompt)
            self.assertEqual(max_completion_tokens, 180)
            return {
                "model": "gpt-4o-mini-2024-07-18",
                "text": (
                    '{"builder":40,"specificity":30,"substance":20,"total":90,'
                    '"action":"comment","reason":"agent_tool_discovery",'
                    '"tool_discovery":true,"topic":"agent"}'
                ),
            }

        result = score_post_via_portkey(item(), complete_fn=complete)
        self.assertEqual(result["action"], "comment")
        self.assertEqual(result["model"], "gpt-4o-mini-2024-07-18")

    def test_live_score_fails_closed_on_invalid_result(self):
        def complete(_prompt, max_completion_tokens):
            self.assertEqual(max_completion_tokens, 180)
            return {"model": "gpt-4o-mini", "text": '{"action":"comment"}'}

        with self.assertRaises(RuntimeError):
            score_post_via_portkey(item(), complete_fn=complete)

    def test_agent_builder_is_worth_a_comment(self):
        result = score_post(item())
        self.assertEqual(result["action"], "comment")
        self.assertEqual(result["reason"], "agent_tool_discovery")
        self.assertTrue(result["tool_discovery"])
        self.assertGreaterEqual(result["builder"], 10)
        self.assertGreaterEqual(result["specificity"], 10)
        self.assertGreaterEqual(result["total"], 40)

    def test_generic_developer_is_skipped(self):
        result = score_post(item(text="hiring a software engineer for our backend team"))
        self.assertEqual(result["action"], "skip")
        self.assertEqual(result["reason"], "generic_or_consumer")

    def test_consumer_chat_is_skipped(self):
        result = score_post(item(text="I asked chatgpt to write this launch email"))
        self.assertEqual(result["action"], "skip")
        self.assertEqual(result["reason"], "generic_or_consumer")

    def test_framework_name_drop_is_not_enough(self):
        result = score_post(item(text="just tried langchain today"))
        self.assertEqual(result["action"], "skip")
        self.assertIn(result["reason"], ("below_comment_floor", "generic_or_consumer"))

    def test_other_networks_go_to_later_use(self):
        for channel in ("hacker_news", "github", "product_hunt", "apollo"):
            result = score_post(item(channel))
            self.assertEqual(result["action"], "later_use", channel)
            self.assertGreaterEqual(result["specificity"], 20)

    def test_agent_runtime_without_tools_is_not_a_comment(self):
        result = score_post(
            item(
                text=(
                    "we shipped our voice agent last night and the eval harness "
                    "caught a broken handoff."
                )
            )
        )
        self.assertEqual(result["action"], "skip")
        self.assertEqual(result["reason"], "agent_not_tool_discovery")
        self.assertFalse(result["tool_discovery"])

    def test_agent_topic_without_tools_is_not_a_comment(self):
        result = score_post(item(text="we launched our ai agent yesterday and I am so excited"))
        self.assertEqual(result["action"], "skip")
        self.assertEqual(result["reason"], "agent_not_tool_discovery")
        self.assertEqual(result["topic"], "agent")

    def test_assistant_topic_needs_building_and_tools(self):
        using = score_post(item(text="my assistant summarized the standup notes this morning"))
        self.assertEqual(using["action"], "skip")
        self.assertEqual(using["reason"], "assistant_not_building")
        building = score_post(
            item(text="we are building an assistant that files tickets from email")
        )
        self.assertEqual(building["action"], "skip")
        self.assertEqual(building["reason"], "assistant_not_tool_discovery")
        tools = score_post(
            item(
                text=(
                    "we are building an assistant that uses a tool catalog "
                    "instead of loading every schema up front"
                )
            )
        )
        self.assertEqual(tools["action"], "comment")
        self.assertEqual(tools["reason"], "assistant_tool_discovery")
        self.assertTrue(tools["tool_discovery"])

    def test_third_person_mcp_post_is_worth_a_comment(self):
        result = score_post(
            item(
                text=(
                    "the agent discovers tools at runtime through mcp instead of "
                    "loading the whole catalog up front. tool calling stays bounded."
                )
            )
        )
        self.assertEqual(result["action"], "comment")
        self.assertTrue(result["tool_discovery"] or result["reason"] == "agent_builder_runtime")

    def test_x402_agent_payments_post_is_worth_a_comment(self):
        result = score_post(
            item(
                text=(
                    "we wired x402 so the agent can discover payment tools "
                    "instead of hardcoding every checkout schema."
                )
            )
        )
        self.assertEqual(result["action"], "comment")
        self.assertEqual(result["reason"], "agent_tool_discovery")
        self.assertTrue(result["tool_discovery"])

    def test_protocol_family_posts_are_worth_a_comment(self):
        for text in (
            "we wired a2a so the agent can discover the other agent's tools at runtime.",
            "the in-browser editor lets the agent work through webmcp tools instead of a dashboard.",
            "ucp is how the agent finds checkout instead of hardcoding every merchant schema.",
        ):
            result = score_post(item(text=text))
            self.assertEqual(result["action"], "comment", text)
            self.assertEqual(result["reason"], "agent_tool_discovery", text)
            self.assertTrue(result["tool_discovery"], text)

    def test_asking_which_mcp_or_what_tools_is_comment(self):
        which = score_post(
            item(
                text=(
                    "Which MCP server did you use for the 3D model conversion? "
                    "Did it call out to an existing 3D modeling tool?"
                )
            )
        )
        self.assertEqual(which["action"], "comment")
        self.assertTrue(which["tool_discovery"])
        exposed = score_post(
            item(
                text=(
                    "A SoundCloud MCP server is a fun one. What tools did you expose "
                    "just search/stream metadata, or playlists too?"
                )
            )
        )
        self.assertEqual(exposed["action"], "comment")
        self.assertTrue(exposed["tool_discovery"])

    def test_protocol_name_without_tool_discovery_skips(self):
        for text in (
            "we shipped a2a today with google",
            "just launched our mcp server",
            "ucp is the new standard from the commerce group",
            "x402 volume is up this week",
        ):
            result = score_post(item(text=text))
            self.assertEqual(result["action"], "skip", text)
            self.assertEqual(result["reason"], "agent_not_tool_discovery", text)
            self.assertFalse(result["tool_discovery"], text)

    def test_airdrop_promo_still_skips(self):
        result = score_post(item(text="x402 airdrop and token sale this week"))
        self.assertEqual(result["action"], "skip")
        self.assertEqual(result["reason"], "excluded_promotion")

    def test_x_pro_tool_discovery_thread_is_worth_a_comment(self):
        result = score_post(
            item(
                text=(
                    "3. Make tool discovery part of the agent’s toolkit. "
                    "Our tool catalog has 200+ tools. Their full read/write schemas "
                    "measured ~314K tokens. We wanted it to find and load the tools "
                    "it needed instead of listing them all upfront."
                )
            )
        )
        self.assertEqual(result["action"], "comment")
        self.assertEqual(result["reason"], "agent_tool_discovery")
        self.assertTrue(result["tool_discovery"])

    def test_github_bots_are_skipped(self):
        result = score_post(
            item("github", name="dependabot[bot]", profile_url="https://github.com/dependabot")
        )
        self.assertEqual(result["action"], "skip")
        self.assertEqual(result["reason"], "bot_identity")


if __name__ == "__main__":
    unittest.main()
