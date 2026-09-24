"""Small Smartlead client used by the manual email agent."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request


class SmartleadError(RuntimeError):
    """Smartlead returned an HTTP or response error."""


class SmartleadClient:
    def __init__(
        self,
        api_key,
        *,
        base_url="https://server.smartlead.ai/api/v1",
        timeout=30,
        transport=None,
    ):
        if not str(api_key or "").strip():
            raise ValueError("SMARTLEAD_API_KEY required")
        self.api_key = str(api_key).strip()
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.transport = transport

    def request(self, method, path, *, params=None, payload=None):
        query = dict(params or {})
        query["api_key"] = self.api_key
        url = f"{self.base_url}{path}?{urllib.parse.urlencode(query)}"
        if self.transport:
            return self.transport(method, path, query, payload)
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=body,
            method=method,
            headers={
                "Content-Type": "application/json",
                "User-Agent": "Darwin-GTM-Email-Agent/1.0",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                data = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise SmartleadError(f"Smartlead HTTP {exc.code}: {detail[:500]}") from exc
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            raise SmartleadError("Smartlead transport failed; outcome requires readback") from exc
        try:
            return json.loads(data) if data else {}
        except json.JSONDecodeError as exc:
            raise SmartleadError("Smartlead returned invalid JSON") from exc

    def list_campaigns(self):
        data = self.request("GET", "/campaigns/")
        if isinstance(data, list):
            return data
        for key in ("data", "campaigns"):
            if isinstance(data.get(key), list):
                return data[key]
        raise SmartleadError("unsupported campaign list response")

    def find_campaign(self, name):
        matches = [row for row in self.list_campaigns() if row.get("name") == name]
        if len(matches) > 1:
            raise SmartleadError(f"multiple Smartlead campaigns named {name!r}")
        return matches[0] if matches else None

    def create_campaign(self, name):
        data = self.request("POST", "/campaigns/create", payload={"name": name})
        campaign = data.get("campaign", data.get("data", data))
        if not isinstance(campaign, dict) or campaign.get("id") is None:
            raise SmartleadError("campaign creation response did not include an id")
        return campaign

    def get_campaign(self, campaign_id):
        return self.request("GET", f"/campaigns/{campaign_id}")

    def configure_campaign(self, campaign_id, config):
        settings = {
            "name": config["name"],
            "track_settings": ["DONT_TRACK_EMAIL_OPEN", "DONT_TRACK_LINK_CLICK"],
            "send_as_plain_text": True,
            "follow_up_percentage": 0,
            "enable_ai_esp_matching": False,
            "domain_level_rate_limit": True,
            "add_unsubscribe_tag": True,
            "unsubscribe_text": config["unsubscribe_text"],
        }
        self.request("POST", f"/campaigns/{campaign_id}/settings", payload=settings)
        sequence = {
            "sequences": [
                {
                    "id": None,
                    "seq_number": 1,
                    "subject": "{{email_subject}}",
                    "email_body": "{{email_body}}",
                    "seq_delay_details": {"delay_in_days": 0},
                }
            ]
        }
        self.request("POST", f"/campaigns/{campaign_id}/sequences", payload=sequence)
        schedule = {
            "timezone": config["timezone"],
            "days_of_the_week": config["days"],
            "start_hour": config["start_hour"],
            "end_hour": config["end_hour"],
            "min_time_btw_emails": config["min_time_between_emails"],
            "max_new_leads_per_day": config["daily_message_limit"],
        }
        self.request("POST", f"/campaigns/{campaign_id}/schedule", payload=schedule)
        account_ids = config.get("email_account_ids", [])
        if account_ids:
            self.request(
                "POST",
                f"/campaigns/{campaign_id}/email-accounts",
                payload={"email_account_ids": account_ids},
            )

    def activate_campaign(self, campaign_id):
        """Explicitly activate one campaign; never called by provisioning or enrollment."""
        return self.request(
            "PATCH", f"/campaigns/{campaign_id}/status", payload={"status": "ACTIVE"}
        )

    def add_leads(self, campaign_id, leads):
        results = []
        for offset in range(0, len(leads), 400):
            payload = {
                "lead_list": leads[offset : offset + 400],
                "settings": {
                    "ignore_duplicate_leads_in_other_campaign": False,
                    "ignore_global_block_list": False,
                    "return_lead_ids": True,
                },
            }
            results.append(self.request("POST", f"/campaigns/{campaign_id}/leads", payload=payload))
        return results

    def campaign_analytics(self, campaign_id):
        return self.request("GET", f"/campaigns/{campaign_id}/analytics")

    def sent_messages(self, campaign_id):
        rows = []
        offset = 0
        while True:
            data = self.request(
                "POST",
                "/master-inbox/sent",
                payload={
                    "offset": offset,
                    "limit": 20,
                    "filters": {"campaignId": campaign_id},
                    "sortBy": "SENT_TIME_DESC",
                },
            )
            page = data.get("messages", data.get("data"))
            if not isinstance(page, list):
                raise SmartleadError("unsupported sent-message response")
            rows.extend(page)
            total = data.get("total_count")
            if not page or (total is not None and len(rows) >= int(total)):
                return rows
            if total is None and len(page) < int(data.get("limit", 20)):
                return rows
            offset += len(page)

    def campaign_leads(self, campaign_id):
        rows = []
        offset = 0
        while True:
            data = self.request(
                "GET",
                f"/campaigns/{campaign_id}/leads",
                params={"offset": offset, "limit": 100},
            )
            page = data.get("data", data.get("leads", []))
            if not isinstance(page, list):
                raise SmartleadError("unsupported campaign lead response")
            rows.extend(page)
            total = data.get("total_leads", data.get("total", data.get("total_count")))
            if (
                not page
                or (total is not None and len(rows) >= int(total))
                or (total is None and len(page) < 100)
            ):
                return rows
            offset += len(page)
