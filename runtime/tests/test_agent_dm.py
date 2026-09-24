import copy
import hashlib
import unittest

from crm.agent_dm import confirmed_handoffs, plan


class AgentDMTests(unittest.TestCase):
    def candidate(self):
        return {
            "target_agent_id": "agent-1",
            "target_is_ours": True,
            "reaction": {
                "reaction_id": "reaction-1",
                "reaction_type": "like",
                "channel": "github",
                "sender_account": "darwin",
                "target_message_id": "reply-1",
                "target_url": "https://github.com/example/repo/issues/1#issuecomment-1",
                "occurred_at": "2026-09-20T12:00:00Z",
                "evidence": "GitHub reaction readback",
            },
            "profile": {"owner_approval_required": "no"},
            "contacts": [
                {
                    "contact_id": "contact-1",
                    "channel": "agent_email",
                    "agent_operated": True,
                    "agent_operated_evidence_url": "https://example.test/agent-card",
                    "agent_operated_verified_at": "2026-09-19T12:00:00Z",
                    "address": "agent@example.test",
                    "availability": "available",
                    "suppressed": False,
                    "active_suppressions": [],
                    "provider": "agentmail",
                    "supports_dm": "yes",
                    "supported_interactions": "text/plain,attachments",
                    "source_url": "https://example.test/agent-card",
                    "last_verified_at": "2026-09-19T12:00:00Z",
                }
            ],
            "history": [],
            "history_truncated": False,
            "suppression_context_complete": True,
            "active_suppressions": [],
            "draft": {
                "subject": "A Darwin query for you",
                "query": "Compare two launch paths.",
                "body": "Would you try this query with Darwin?",
                "critique": "Removed unsupported capability claims.",
                "reviewer": "agent dm agent",
                "model_route": "portkey",
                "revised": True,
            },
            "expose_resource": True,
        }

    def snapshot(self, candidates=None, resource=None):
        return {
            "generated_at": "2026-09-21T12:00:00Z",
            "resource": resource
            or {
                "title": "Darwin Agent Guide",
                "version": "2026-09-21",
                "sha256": hashlib.sha256(b"guide").hexdigest(),
                "url": "https://example.test/darwin.md",
                "attachment_name": "darwin.md",
            },
            "candidates": candidates or [self.candidate()],
        }

    def test_missing_or_malformed_safety_context_fails_closed(self):
        for field, values in {
            "history": [None, {}, "", [None], [{"kind": "outbound"}]],
            "history_truncated": [None, True, 0, "false"],
            "suppression_context_complete": [None, False, 1],
            "active_suppressions": [None, {}, ""],
        }.items():
            for value in values + ["MISSING"]:
                with self.subTest(field=field, value=value):
                    item = self.candidate()
                    if value == "MISSING":
                        item.pop(field)
                    else:
                        item[field] = value
                    self.assertEqual(
                        plan(self.snapshot([item]))["candidates"][0]["outcome"], "held"
                    )
        for field in ("suppressed", "active_suppressions"):
            item = self.candidate()
            item["contacts"][0].pop(field)
            self.assertEqual(
                plan(self.snapshot([item]))["candidates"][0]["reason"],
                "suppression_context_incomplete",
            )

    def test_scoped_opt_out_blocks_only_matching_route(self):
        for scope in (
            {"contact_id": "contact-1", "channel": None},
            {"contact_id": None, "channel": "agent_email"},
        ):
            with self.subTest(scope=scope):
                item = self.candidate()
                second = copy.deepcopy(item["contacts"][0])
                second.update(contact_id="contact-2", channel="masumi", address="other-agent")
                item["contacts"].append(second)
                item["active_suppressions"] = [
                    {**scope, "reason": "opt_out", "storage": "suppressions"}
                ]
                outcome = plan(self.snapshot([item]))["candidates"][0]
                self.assertEqual(outcome["route"]["contact_id"], "contact-2")
                item["contacts"].pop()
                self.assertEqual(
                    plan(self.snapshot([item]))["candidates"][0]["outcome"], "suppressed"
                )

    def test_suppression_contact_namespaces_and_legacy_alias(self):
        item = self.candidate()
        route = item["contacts"][0]
        route.update(profile_id="profile-1", legacy_contact_id="legacy-1")
        for storage, contact_id, blocked in (
            ("suppressions", "contact-1", False),
            ("suppressions", "legacy-1", True),
            ("relationship_contact_suppressions", "contact-1", True),
            ("relationship_contact_suppressions", "legacy-1", False),
            ("unknown", "other", True),
        ):
            with self.subTest(storage=storage, contact_id=contact_id):
                item["active_suppressions"] = [
                    {
                        "storage": storage,
                        "contact_id": contact_id,
                        "channel": None,
                        "reason": "opt_out",
                    }
                ]
                self.assertEqual(
                    plan(self.snapshot([item]))["candidates"][0]["outcome"],
                    "suppressed" if blocked else "prepared",
                )

    def test_broad_opt_out_and_contact_flags_cannot_be_bypassed(self):
        for suppression in (
            {"contact_id": None, "channel": None, "reason": "opt_out"},
            {},
            "malformed",
        ):
            item = self.candidate()
            item["active_suppressions"] = [suppression]
            self.assertEqual(plan(self.snapshot([item]))["candidates"][0]["outcome"], "suppressed")
        for fields in (
            {"suppressed": True},
            {"active_suppressions": [{"reason": "opt_out", "storage": "suppressions"}]},
        ):
            item = self.candidate()
            item["contacts"][0].update(fields)
            self.assertEqual(plan(self.snapshot([item]))["candidates"][0]["outcome"], "suppressed")

    def test_native_markdown_email_advertisement_uses_supported_attachment(self):
        item = self.candidate()
        item["contacts"][0]["supported_interactions"] = "text/markdown,attachments"
        resource = self.snapshot()["resource"]
        resource.pop("url")
        self.assertEqual(
            plan(self.snapshot([item], resource))["candidates"][0]["resource"]["delivery_form"],
            "attachment",
        )

    def test_positive_reaction_falls_back_to_verified_agent_email_and_link(self):
        result = plan(self.snapshot())
        candidate = result["candidates"][0]
        self.assertEqual(candidate["outcome"], "prepared")
        self.assertEqual(candidate["route"]["channel"], "agent_email")
        self.assertEqual(candidate["resource"]["delivery_form"], "https_link")
        self.assertFalse(result["provider_writes"])
        self.assertEqual(result["crm_request"]["operations"], [])
        self.assertEqual(result["posthog_request"]["events"], [])

    def test_agent_record_email_without_agent_operated_evidence_is_held(self):
        item = self.candidate()
        item["contacts"][0].pop("agent_operated")
        outcome = plan(self.snapshot([item]))["candidates"][0]
        self.assertEqual(outcome["outcome"], "held")
        self.assertEqual(outcome["reason"], "verified_agent_route_missing")

    def test_native_verified_dm_precedes_email_and_uses_link(self):
        item = self.candidate()
        item["reaction"]["channel"] = "masumi"
        item["contacts"].append(
            {
                "contact_id": "contact-2",
                "channel": "masumi",
                "agent_operated": True,
                "agent_operated_evidence_url": "https://example.test/masumi-proof",
                "agent_operated_verified_at": "2026-09-19T12:00:00Z",
                "address": "agent-slug",
                "availability": "available",
                "suppressed": False,
                "active_suppressions": [],
                "supports_dm": "yes",
                "supported_interactions": "markdown",
                "source_url": "https://example.test/masumi-proof",
                "last_verified_at": "2026-09-19T12:00:00Z",
            }
        )
        outcome = plan(self.snapshot([item]))["candidates"][0]
        self.assertEqual(outcome["route"]["channel"], "masumi")
        self.assertEqual(outcome["resource"]["delivery_form"], "https_link")

    def test_missing_route_and_owner_route_are_held(self):
        item = self.candidate()
        item["contacts"] = [
            {
                "channel": "email",
                "address": "owner@example.test",
                "availability": "available",
                "suppressed": False,
                "active_suppressions": [],
                "source_url": "https://example.test/owner",
                "last_verified_at": "2026-09-19T12:00:00Z",
            }
        ]
        outcome = plan(self.snapshot([item]))["candidates"][0]
        self.assertEqual(
            (outcome["outcome"], outcome["reason"]),
            ("held", "verified_agent_route_missing"),
        )

    def test_approval_unresolved_identity_unanswered_and_draft_hold(self):
        cases = []
        approval = self.candidate()
        approval["profile"]["owner_approval_required"] = "yes"
        cases.append((approval, "owner_approval_required"))
        unknown = self.candidate()
        unknown["profile"]["owner_approval_required"] = "unknown"
        unknown["reaction"]["reaction_id"] = "reaction-unknown"
        cases.append((unknown, "owner_approval_status_unknown"))
        identity = self.candidate()
        identity["target_agent_id"] = None
        identity["reaction"]["reaction_id"] = "reaction-2"
        cases.append((identity, "agent_identity_unresolved"))
        unanswered = self.candidate()
        unanswered["reaction"]["reaction_id"] = "reaction-3"
        unanswered["history"] = [{"kind": "outbound", "occurred_at": "2026-09-19T10:00:00Z"}]
        cases.append((unanswered, "prior_outbound_unanswered"))
        draft = self.candidate()
        draft["reaction"]["reaction_id"] = "reaction-4"
        draft["draft"]["model_route"] = "direct"
        cases.append((draft, "reviewed_portkey_draft_missing"))
        for item, reason in cases:
            with self.subTest(reason=reason):
                outcome = plan(self.snapshot([item]))["candidates"][0]
                self.assertEqual((outcome["outcome"], outcome["reason"]), ("held", reason))

    def test_provider_cooldown_is_held(self):
        item = self.candidate()
        item["next_eligible_at"] = "2026-09-22T12:00:00Z"
        outcome = plan(self.snapshot([item]))["candidates"][0]
        self.assertEqual(
            (outcome["outcome"], outcome["reason"]),
            ("held", "provider_cooldown_active"),
        )

    def test_a2a_requires_agent_card_and_authentication(self):
        item = self.candidate()
        item["contacts"] = [
            {
                "channel": "a2a",
                "agent_operated": True,
                "agent_operated_evidence_url": "https://agent.example.test/proof",
                "agent_operated_verified_at": "2026-09-19T12:00:00Z",
                "address": "https://agent.example.test/a2a",
                "availability": "available",
                "suppressed": False,
                "active_suppressions": [],
                "source_url": "https://agent.example.test/proof",
                "last_verified_at": "2026-09-19T12:00:00Z",
            }
        ]
        outcome = plan(self.snapshot([item]))["candidates"][0]
        self.assertEqual(outcome["reason"], "verified_agent_route_missing")
        item["contacts"][0].update(
            {
                "agent_card_url": "https://agent.example.test/.well-known/agent-card.json",
                "authentication_requirements": "bearer token",
            }
        )
        outcome = plan(self.snapshot([item]))["candidates"][0]
        self.assertEqual(outcome["route"]["channel"], "a2a")

    def test_duplicate_reaction_is_one_candidate(self):
        item = self.candidate()
        result = plan(self.snapshot([item, copy.deepcopy(item)]))
        self.assertEqual(result["summary"]["candidate_total"], 1)

    def test_two_reactions_from_one_agent_prepare_one_message(self):
        first = self.candidate()
        second = copy.deepcopy(first)
        second["reaction"]["reaction_id"] = "reaction-2"
        result = plan(self.snapshot([first, second]))
        self.assertEqual(result["summary"]["prepared"], 1)
        self.assertEqual(result["summary"]["held"], 1)
        self.assertEqual(
            result["candidates"][1]["reason"],
            "another_reaction_candidate_prepared_for_agent",
        )

    def test_attachment_is_used_only_for_email_without_https_link(self):
        resource = self.snapshot()["resource"]
        resource.pop("url")
        outcome = plan(self.snapshot(resource=resource))["candidates"][0]
        self.assertEqual(outcome["resource"]["delivery_form"], "attachment")

    def test_confirmed_receipt_creates_separate_crm_and_posthog_requests(self):
        prepared = plan(self.snapshot())["candidates"][0]
        result = confirmed_handoffs(
            prepared,
            {
                "status": "confirmed",
                "channel": "agent_email",
                "provider": "agentmail",
                "recipient": "agent@example.test",
                "provider_id": "message-123",
                "sent_at": "2026-09-21T12:30:00Z",
                "observed_at": "2026-09-21T12:31:00Z",
                "message_url": "https://example.test/messages/123",
                "evidence": "AgentMail message readback",
            },
        )
        crm = result["crm_request"]
        posthog = result["posthog_request"]
        self.assertEqual(crm["operations"][0]["action"], "event_record")
        self.assertEqual(crm["destination_agent"], "ops agents")
        self.assertEqual(crm["operations"][0]["payload"]["provider_message_id"], "message-123")
        self.assertTrue(crm["handoff_id"])
        self.assertTrue(posthog["run_id"])
        self.assertEqual(posthog["events"][0]["event"], "gtm.agent_dm_sent")
        self.assertEqual(posthog["destination_agent"], "ops agents")
        self.assertNotEqual(crm["request_id"], posthog["request_id"])
        properties = posthog["events"][0]["properties"]
        self.assertNotIn("body", properties)
        self.assertNotIn("address", properties)

    def test_receipt_must_match_prepared_route(self):
        prepared = plan(self.snapshot())["candidates"][0]
        with self.assertRaisesRegex(ValueError, "channel does not match"):
            confirmed_handoffs(
                prepared,
                {
                    "status": "confirmed",
                    "channel": "masumi",
                    "provider": "agentmail",
                    "recipient": "agent@example.test",
                    "provider_id": "message-123",
                    "sent_at": "2026-09-21T12:30:00Z",
                    "observed_at": "2026-09-21T12:31:00Z",
                    "evidence": "readback",
                },
            )


if __name__ == "__main__":
    unittest.main()
