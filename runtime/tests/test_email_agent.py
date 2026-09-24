import unittest
from unittest.mock import patch

from crm.email_agent import prepare_candidates, provider_state, run, smartlead_lead, utc_now
from crm.smartlead import SmartleadClient, SmartleadError


def candidate(index=1, email=None):
    return {
        "email_verified": True,
        "email_verification_evidence": "https://example.com/contact",
        "identity_status": "verified",
        "suppression_context_complete": True,
        "suppression_checked_at": utc_now(),
        "active_suppressions": [],
        "person_id": f"person_{index}",
        "contact_id": f"contact_{index}",
        "email": email or f"person{index}@example.com",
        "first_name": "Test",
        "last_name": str(index),
        "company_name": "Example",
        "template_id": "darwin-query-intro",
        "copy_version": "v1",
        "email_subject": "a useful query",
        "email_body": "Here is a relevant Darwin query.",
        "custom_fields": {"lead_source": "test-import"},
    }


def config(campaign_id=123):
    return {
        "campaign": {
            "id": campaign_id,
            "name": "darwin-main-email-agent",
            "daily_message_limit": 10000,
            "timezone": "UTC",
            "days": [0, 1, 2, 3, 4, 5, 6],
            "start_hour": "00:00",
            "end_hour": "23:59",
            "min_time_between_emails": 10,
            "unsubscribe_text": "Unsubscribe: {{unsubscribe}}",
            "email_account_ids": [10, 11],
        }
    }


class FakeSmartlead:
    def __init__(self, *, existing=True):
        self.calls = []
        self.existing = existing
        self.imported = []

    def find_campaign(self, name):
        self.calls.append(("find", name))
        return None

    def create_campaign(self, name):
        self.calls.append(("create", name))
        return {"id": 456, "name": name, "status": "DRAFTED"}

    def configure_campaign(self, campaign_id, campaign):
        self.calls.append(("configure", campaign_id, campaign))

    def add_leads(self, campaign_id, leads):
        self.calls.append(("add_leads", campaign_id, leads))
        self.imported.extend({"lead": dict(lead, id=1000 + i)} for i, lead in enumerate(leads))
        return [{"added_count": len(leads)}]

    def activate_campaign(self, campaign_id):
        self.calls.append(("activate", campaign_id))
        return {"ok": True, "status": "STARTED"}

    def campaign_leads(self, campaign_id):
        self.calls.append(("campaign_leads", campaign_id))
        if not self.existing:
            return self.imported
        row = candidate()
        row["idempotency_key"] = "fixture"
        return self.imported + [
            {
                "lead": dict(smartlead_lead(row), id=900),
                "email_status": "REPLIED",
                "campaign_lead_map_id": 901,
                "updated_at": "2026-09-22T12:00:00Z",
            }
        ]

    def campaign_analytics(self, campaign_id):
        self.calls.append(("analytics", campaign_id))
        return {"sent_count": 1, "reply_count": 1}

    def sent_messages(self, campaign_id):
        self.calls.append(("sent_messages", campaign_id))
        return [
            {
                "id": "message_1",
                "campaign_lead_map_id": 901,
                "lead": {"email": "person1@example.com"},
                "email_account": {"email": "actual-sender@example.com"},
                "last_message": {
                    "sent_at": "2026-09-22T11:00:00Z",
                    "replied_at": "2026-09-22T12:00:00Z",
                },
                "email_status": "Replied",
            }
        ]


class EmailAgentTests(unittest.TestCase):
    def test_fixture_run_writes_crm_operation_and_posthog_analytics(self):
        result = run(config(), {"human_email": [candidate()]})
        handoff = result["crm_handoff"]
        self.assertEqual(handoff["source_agent"], "email agent")
        self.assertEqual(handoff["operations"], [])
        posthog = result["posthog_handoff"]
        self.assertEqual(posthog["events"][0]["event"], "gtm.email_agent_run")
        self.assertEqual(posthog["events"][0]["properties"]["counts"], {"prepared": 1})
        self.assertIn("uuid", posthog["events"][0])
        self.assertNotIn("operations", posthog)

    def test_duplicate_address_is_prepared_once(self):
        rows = prepare_candidates(
            {"human_email": [candidate(1), candidate(1, "PERSON1@example.com")]}, 123
        )
        self.assertEqual(len(rows), 1)

    def test_provision_configures_campaign_without_activation(self):
        client = FakeSmartlead()
        result = run(
            config(None),
            {"human_email": [candidate()]},
            client=client,
            provision_campaign=True,
        )
        self.assertEqual(result["campaign_id"], "456")
        self.assertEqual([call[0] for call in client.calls], ["find", "create", "configure"])

    def test_enrollment_uses_smartlead_custom_fields(self):
        client = FakeSmartlead(existing=False)
        result = run(config(), {"human_email": [candidate()]}, client=client, enroll=True)
        lead = next(call for call in client.calls if call[0] == "add_leads")[2][0]
        self.assertEqual(lead["custom_fields"]["crm_contact_id"], "contact_1")
        self.assertEqual(lead["custom_fields"]["lead_source"], "test-import")
        self.assertEqual(lead["custom_fields"]["email_subject"], "a useful query")
        self.assertEqual(lead["custom_fields"]["email_body"], "Here is a relevant Darwin query.")
        self.assertEqual(result["crm_handoff"]["operations"], [])

    def test_activation_is_explicit_and_requires_same_run_enrollment(self):
        client = FakeSmartlead(existing=False)
        with self.assertRaisesRegex(ValueError, "requires --enroll"):
            run(config(), {"human_email": [candidate()]}, client=client, activate=True)
        result = run(
            config(),
            {"human_email": [candidate()]},
            client=client,
            enroll=True,
            activate=True,
        )
        self.assertEqual(result["activation_receipt"]["status"], "STARTED")
        self.assertEqual(
            [call[0] for call in client.calls],
            ["campaign_leads", "add_leads", "campaign_leads", "activate"],
        )

    def test_enrollment_rerun_skips_addresses_already_in_campaign(self):
        client = FakeSmartlead(existing=True)
        result = run(
            config(),
            {"human_email": [candidate(1), candidate(2)]},
            client=client,
            enroll=True,
        )
        added = next(call for call in client.calls if call[0] == "add_leads")[2]
        self.assertEqual([lead["email"] for lead in added], ["person2@example.com"])
        self.assertEqual(result["prepared"], 2)

    def test_reconcile_maps_provider_outcome(self):
        client = FakeSmartlead()
        row = candidate()
        result = run(config(), {"human_email": [row]}, client=client, reconcile=True)
        payload = result["crm_handoff"]["operations"][0]["payload"]
        self.assertEqual(payload["state"], "replied")
        self.assertEqual(payload["provider_lead_id"], 900)
        self.assertEqual(payload["sender_account"], "actual-sender@example.com")
        self.assertEqual(payload["provider_message_id"], "message_1")
        self.assertEqual(payload["occurred_at"], "2026-09-22T12:00:00Z")
        self.assertEqual(result["crm_handoff"]["summary"]["counts"], {"replied": 1})
        self.assertEqual(
            result["posthog_handoff"]["events"][0]["properties"]["provider_analytics"],
            {"sent_count": 1, "reply_count": 1},
        )

    def test_sent_and_delivered_remain_distinct_crm_states(self):
        self.assertEqual(provider_state({"email_status": "SENT"}), "sent")
        self.assertEqual(provider_state({"email_status": "DELIVERED"}), "delivered")
        self.assertEqual(provider_state({"status": "COMPLETED"}), "sent")

    def test_campaign_status_alone_does_not_claim_a_send(self):
        client = FakeSmartlead()
        client.sent_messages = lambda campaign_id: []
        result = run(config(), {"human_email": [candidate()]}, client=client, reconcile=True)
        self.assertEqual(result["crm_handoff"]["operations"], [])
        self.assertEqual(result["crm_handoff"]["summary"]["counts"], {"uncertain": 1})

    def test_posthog_handoff_excludes_provider_contact_and_body(self):
        client = FakeSmartlead()
        client.campaign_analytics = lambda campaign_id: {
            "sent_count": 1,
            "client_email": "private@example.com",
            "email_body": "private message",
        }
        result = run(config(), {"human_email": [candidate()]}, client=client, reconcile=True)
        analytics = result["posthog_handoff"]["events"][0]["properties"]["provider_analytics"]
        self.assertEqual(analytics, {"sent_count": 1})

    def test_smartlead_batches_four_hundred_and_never_activates(self):
        calls = []

        def transport(method, path, query, payload):
            calls.append((method, path, query, payload))
            return {"ok": True, "added_count": len(payload.get("lead_list", []))}

        client = SmartleadClient("secret", transport=transport)
        client.configure_campaign(123, config()["campaign"])
        client.add_leads(123, [{"email": f"person{i}@example.com"} for i in range(401)])
        paths = [call[1] for call in calls]
        self.assertNotIn("/campaigns/123/status", paths)
        settings = next(call for call in calls if call[1] == "/campaigns/123/settings")
        self.assertEqual(settings[0], "POST")
        schedule = next(call for call in calls if call[1] == "/campaigns/123/schedule")
        self.assertEqual(schedule[3]["max_new_leads_per_day"], 10000)
        self.assertEqual(schedule[3]["days_of_the_week"], [0, 1, 2, 3, 4, 5, 6])
        self.assertEqual(paths.count("/campaigns/123/leads"), 2)
        sizes = [len(call[3]["lead_list"]) for call in calls if call[1] == "/campaigns/123/leads"]
        self.assertEqual(sizes, [400, 1])

    def test_partial_import_readback_and_safe_rerun(self):
        client = FakeSmartlead(existing=False)
        original = client.add_leads

        def partial(campaign, leads):
            original(campaign, leads[:1])
            raise SmartleadError("later batch failed")

        client.add_leads = partial
        snapshot = {"human_email": [candidate(1), candidate(2)]}
        result = run(config(), snapshot, client=client, enroll=True)
        self.assertEqual(
            [r["state"] for r in result["recipient_states"]], ["enrolled", "uncertain"]
        )
        self.assertEqual(result["crm_handoff"]["operations"], [])
        client.add_leads = original
        result = run(config(), snapshot, client=client, enroll=True)
        imports = [c[2] for c in client.calls if c[0] == "add_leads"]
        self.assertEqual([r["email"] for r in imports[-1]], ["person2@example.com"])
        self.assertEqual([r["state"] for r in result["recipient_states"]], ["enrolled", "enrolled"])

    def test_partial_import_cannot_activate(self):
        client = FakeSmartlead(existing=False)
        client.add_leads = lambda campaign, leads: [{"added_count": len(leads)}]
        result = run(
            config(), {"human_email": [candidate()]}, client=client, enroll=True, activate=True
        )
        self.assertEqual(result["activation_receipt"]["status"], "blocked")
        self.assertNotIn("activate", [c[0] for c in client.calls])

    def test_missing_route_or_suppression_evidence_blocks_before_provider(self):
        for key, value in (
            ("email_verified", False),
            ("identity_status", "unverified"),
            ("suppression_context_complete", False),
            ("suppressed", True),
            ("active_suppressions", [{"reason": "opt_out"}]),
        ):
            row = candidate()
            row[key] = value
            client = FakeSmartlead()
            with self.assertRaises(ValueError):
                run(config(), {"human_email": [row]}, client=client, enroll=True)
            self.assertEqual(client.calls, [])

    def test_conflicting_duplicate_identity_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "conflicting"):
            prepare_candidates(
                {"human_email": [candidate(), candidate(2, "person1@example.com")]}, 123
            )

    def test_existing_different_copy_is_not_silently_reused(self):
        row = candidate()
        row["email_body"] = "different copy"
        client = FakeSmartlead()
        with self.assertRaisesRegex(ValueError, "identity/copy differs"):
            run(config(), {"human_email": [row]}, client=client, enroll=True)
        self.assertNotIn("add_leads", [c[0] for c in client.calls])

    def test_posthog_has_stable_aggregate_distinct_id(self):
        result = run(config(), {"human_email": [candidate()]})
        self.assertEqual(
            result["posthog_handoff"]["events"][0]["properties"]["distinct_id"],
            "email-agent:smartlead:123",
        )

    def test_reconciliation_does_not_attach_outcome_to_conflicting_identity(self):
        row = candidate()
        row["person_id"] = "different_person"
        result = run(config(), {"human_email": [row]}, client=FakeSmartlead(), reconcile=True)
        self.assertEqual(result["crm_handoff"]["operations"], [])
        self.assertEqual(result["recipient_states"][0]["state"], "uncertain")

    def test_campaign_pagination_without_total_reads_next_full_page(self):
        offsets = []

        def transport(method, path, query, payload):
            offsets.append(query["offset"])
            return {
                "data": [{"lead": {"email": f"p{i}@example.com"}} for i in range(100)]
                if query["offset"] == 0
                else []
            }

        self.assertEqual(
            len(SmartleadClient("secret", transport=transport).campaign_leads(123)), 100
        )
        self.assertEqual(offsets, [0, 100])

    def test_second_transport_batch_failure_keeps_imported_first_batch_on_rerun(self):
        imported = []
        posts = []
        fail = [True]

        def transport(method, path, query, payload):
            if method == "GET":
                return {
                    "data": imported[query["offset"] : query["offset"] + 100],
                    "total_leads": len(imported),
                }
            posts.append(len(payload["lead_list"]))
            if len(posts) == 2 and fail[0]:
                raise SmartleadError("timeout")
            imported.extend({"lead": lead} for lead in payload["lead_list"])
            return {"added_count": len(payload["lead_list"])}

        client = SmartleadClient("secret", transport=transport)
        snapshot = {"human_email": [candidate(i) for i in range(401)]}
        result = run(config(), snapshot, client=client, enroll=True)
        self.assertEqual(
            result["crm_handoff"]["summary"]["counts"], {"enrolled": 400, "uncertain": 1}
        )
        fail[0] = False
        result = run(config(), snapshot, client=client, enroll=True)
        self.assertEqual(posts, [400, 1, 1])
        self.assertEqual(result["crm_handoff"]["summary"]["counts"], {"enrolled": 401})

    def test_missing_message_outcome_evidence_stays_uncertain(self):
        for absent in ("id", "email_account", "last_message"):
            client = FakeSmartlead()
            item = client.sent_messages(123)[0]
            item.pop(absent)
            client.sent_messages = lambda campaign, item=item: [item]
            result = run(config(), {"human_email": [candidate()]}, client=client, reconcile=True)
            self.assertEqual(result["crm_handoff"]["operations"], [])
            self.assertEqual(result["recipient_states"][0]["state"], "uncertain")

    def test_duplicate_suppression_and_unknown_context_block_in_both_orders(self):
        for changed in (
            {"active_suppressions": ["do_not_contact"]},
            {"suppression_context_complete": False},
        ):
            clean = candidate()
            blocked = dict(clean, **changed)
            for rows in ([clean, blocked], [blocked, clean]):
                client = FakeSmartlead(existing=False)
                with self.assertRaises(ValueError):
                    run(config(), {"human_email": rows}, client=client, enroll=True)
                self.assertEqual(client.calls, [])

    def test_stale_suppression_context_blocks_enrollment(self):
        row = dict(candidate(), suppression_checked_at="2000-01-01T00:00:00Z")
        with self.assertRaisesRegex(ValueError, "stale"):
            run(config(), {"human_email": [row]}, client=FakeSmartlead(), enroll=True)

    def test_socket_timeout_normalizes_to_smartlead_error(self):
        with patch("crm.smartlead.urllib.request.urlopen", side_effect=TimeoutError):
            with self.assertRaises(SmartleadError):
                SmartleadClient("secret").add_leads(123, [{"email": "example@example.com"}])

    def test_posthog_snapshot_identity_changes_with_campaign_analytics(self):
        client = FakeSmartlead()
        first = run(config(), {"human_email": [candidate()]}, client=client, reconcile=True)
        same = run(config(), {"human_email": [candidate()]}, client=client, reconcile=True)
        client.campaign_analytics = lambda campaign: {"sent_count": 2, "reply_count": 1}
        changed = run(config(), {"human_email": [candidate()]}, client=client, reconcile=True)
        self.assertEqual(first["run_id"], same["run_id"])
        self.assertNotEqual(first["run_id"], changed["run_id"])

    def test_post_import_readback_failure_returns_uncertainty_and_blocks_activation(self):
        for partial in (False, True):
            client = FakeSmartlead(existing=False)
            reads = []

            def read(campaign):
                reads.append(campaign)
                if len(reads) > 1:
                    raise SmartleadError("read unavailable")
                return []

            client.campaign_leads = read
            original = client.add_leads
            if partial:

                def add(campaign, leads):
                    original(campaign, leads[:1])
                    raise SmartleadError("later batch unavailable")

                client.add_leads = add
            result = run(
                config(),
                {"human_email": [candidate(), candidate(2)]},
                client=client,
                enroll=True,
                activate=True,
            )
            self.assertEqual(result["activation_receipt"]["status"], "blocked")
            self.assertEqual(result["crm_handoff"]["summary"]["counts"], {"uncertain": 2})
            self.assertEqual(result["crm_handoff"]["operations"], [])
            self.assertNotIn("activate", [c[0] for c in client.calls])

    def test_sent_mailbox_data_envelope_and_unknown_shape(self):
        offsets = []

        def transport(method, path, query, payload):
            offsets.append(payload["offset"])
            return {
                "ok": True,
                "data": [{"id": "real-message"}] if payload["offset"] == 0 else [],
                "limit": 1,
            }

        rows = SmartleadClient("secret", transport=transport).sent_messages(123)
        self.assertEqual(rows, [{"id": "real-message"}])
        self.assertEqual(offsets, [0, 1])
        with self.assertRaises(SmartleadError):
            SmartleadClient("secret", transport=lambda *a: {"unexpected": []}).sent_messages(123)

    def test_live_analytics_numeric_strings_are_preserved(self):
        client = FakeSmartlead()
        client.campaign_analytics = lambda campaign: {
            "sent_count": "0",
            "reply_count": "1",
            "email_body": "private",
        }
        result = run(config(), {"human_email": [candidate()]}, client=client, reconcile=True)
        self.assertEqual(
            result["crm_handoff"]["summary"]["provider_analytics"],
            {"sent_count": 0, "reply_count": 1},
        )

    def test_explicit_activation_uses_documented_status_contract(self):
        calls = []

        def transport(method, path, query, payload):
            calls.append((method, path, query, payload))
            return {"ok": True}

        client = SmartleadClient("secret", transport=transport)
        client.activate_campaign(123)
        self.assertEqual(
            calls,
            [("PATCH", "/campaigns/123/status", {"api_key": "secret"}, {"status": "ACTIVE"})],
        )


if __name__ == "__main__":
    unittest.main()
