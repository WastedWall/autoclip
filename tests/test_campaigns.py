"""Campaign data and rules: page parsing, the API, and the path into a job."""

from __future__ import annotations

import json
from collections.abc import Iterator

import pytest
from autoclip import app as app_module
from autoclip import campaigns, paths
from autoclip.app import create_app
from autoclip.db import store
from autoclip.db.models import Job, Source, new_id
from autoclip.jobs import queue as queue_module
from fastapi.testclient import TestClient

ID = "828d6eaf-0938-45fb-b44a-b722b14a595a"
LINK = f"https://contentrewards.com/c/campaigns/{ID}"


def record(**overrides) -> dict:
    data = {
        "id": ID,
        "name": "Podcast Clips",
        "description": "Clip the show.",
        "launchDate": "2026-10-29T01:44:41.000Z",
        "status": "active",
        "organization": {"name": "Host"},
        "requiresApplication": False,
        "metrics": {
            "budgetSpentCents": 400000,
            "creatorCount": 10,
            "approvedSubmissionCount": 4,
            "totalViews": 8000,
        },
        "configuration": {
            "budget": 1000000,
            "payoutModel": [
                {"platform": "tiktok", "rate": 125, "minPayout": 300, "maxPayout": 25000}
            ],
        },
        "briefCounts": {"contentRules": 2, "creatorRequirements": 0, "references": 1},
    }
    return data | overrides


def page(data: dict) -> str:
    flight = f'"queries":[{{"state":{{"data":{json.dumps(data)}}}}}]'
    return f"<script>self.__next_f.push([1,{json.dumps(flight)}])</script>"


@pytest.fixture
def client(autoclip_home, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv(app_module.ENV_NO_WORKER, "1")
    monkeypatch.setattr(campaigns, "fetch_page", lambda campaign: page(record()))
    with TestClient(create_app()) as test_client:
        yield test_client


@pytest.fixture
def source(client: TestClient) -> Source:
    return store.create_source(
        Source(id=new_id(), type="upload", path="C:/video.mp4", title="Test", duration_s=600.0)
    )


class TestLink:
    @pytest.mark.parametrize("link", [LINK, f"contentrewards.com/discover/{ID}", f"{LINK}?r=1"])
    def test_accepts_campaign_links(self, link: str) -> None:
        assert campaigns.campaign_id(link) == ID

    def test_rejects_other_links(self) -> None:
        with pytest.raises(campaigns.CampaignError):
            campaigns.campaign_id("https://example.com/c/campaigns/abc")


class TestParse:
    def test_reads_the_campaign_record(self) -> None:
        campaign = campaigns.parse_campaign(page(record()), ID)

        assert campaign.name == "Podcast Clips"
        assert campaign.host == "Host"
        assert campaign.launched == "2026-10-29"
        assert campaign.locked_items == 3

    def test_null_fields_fall_back(self) -> None:
        data = record(launchDate=None, organization=None, metrics=None, description=None)

        campaign = campaigns.parse_campaign(page(data), ID)

        assert campaign.launched == ""
        assert campaign.host == "Unknown"
        assert campaign.creators == 0

    def test_incomplete_payouts_are_left_out(self) -> None:
        payouts = [{"platform": "tiktok", "rate": None}, {"platform": "youtube"}]
        data = record(configuration={"budget": 100, "payoutModel": payouts})

        assert campaigns.parse_campaign(page(data), ID).payouts == []

    def test_broken_page_data_is_a_clear_error(self) -> None:
        html = '<script>self.__next_f.push([1,"\\q"])</script>'

        with pytest.raises(campaigns.CampaignError, match="unreadable"):
            campaigns.parse_campaign(html, ID)

    def test_page_without_data_is_an_error(self) -> None:
        with pytest.raises(campaigns.CampaignError, match="no campaign data"):
            campaigns.parse_campaign("<html></html>", ID)

    def test_page_for_another_campaign_is_an_error(self) -> None:
        with pytest.raises(campaigns.CampaignError, match="different campaign"):
            campaigns.parse_campaign(page(record(id="other")), ID)

    def test_views_needed_for_a_payout(self) -> None:
        assert campaigns.views_needed(300, 125) == 2400
        assert campaigns.views_needed(300, 0) == 0


class TestBlockedTopics:
    @pytest.mark.parametrize("text", ["Online casino", "Crypto exchange", "sports betting"])
    def test_finds_excluded_topics(self, text: str) -> None:
        campaign = campaigns.parse_campaign(page(record(description=text)), ID)

        assert campaigns.blocked_topics(campaign, "")

    def test_checks_the_host(self) -> None:
        data = record(organization={"name": "Lucky Casino"})

        assert campaigns.blocked_topics(campaigns.parse_campaign(page(data), ID), "") == ["casino"]

    def test_ignores_words_that_only_contain_them(self) -> None:
        campaign = campaigns.parse_campaign(page(record(description="The alphabet, cryptic")), ID)

        assert campaigns.blocked_topics(campaign, "") == []

    def test_checks_pasted_rules(self) -> None:
        campaign = campaigns.parse_campaign(page(record()), ID)

        assert campaigns.blocked_topics(campaign, "Promote our sportsbook") == ["sportsbook"]


class TestApi:
    def test_read_returns_data_and_always_the_standing_rules(self, client: TestClient) -> None:
        body = client.post("/api/campaigns", json={"link": LINK}).json()

        assert body["name"] == "Podcast Clips"
        assert body["payouts"][0]["min_views"] == 2400
        assert body["standing_rules"] == campaigns.STANDING_RULES
        assert body["rules_complete"] is False

    def test_read_saves_a_dated_copy(self, client: TestClient) -> None:
        client.post("/api/campaigns", json={"link": LINK})

        assert list(paths.campaigns_dir().glob("*/data-*.json"))

    def test_campaign_folder_can_live_elsewhere(self, client, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv(paths.ENV_CAMPAIGNS, str(tmp_path / "repo"))

        client.post("/api/campaigns", json={"link": LINK})

        assert list((tmp_path / "repo").glob("*/campaign.json"))

    def test_bad_link_is_a_clear_error(self, client: TestClient) -> None:
        response = client.post("/api/campaigns", json={"link": "https://example.com"})

        assert response.status_code == 400
        assert "not a Content Rewards" in response.json()["detail"]

    def test_saved_rules_complete_the_campaign(self, client: TestClient) -> None:
        client.post("/api/campaigns", json={"link": LINK})

        body = client.put(f"/api/campaigns/{ID}/rules", json={"rules": "Tag @show"}).json()

        assert body["rules"] == "Tag @show"
        assert body["rules_complete"] is True
        assert client.get(f"/api/campaigns/{ID}").json()["rules"] == "Tag @show"

    def test_saved_campaigns_are_listed(self, client: TestClient) -> None:
        client.post("/api/campaigns", json={"link": LINK})

        assert [item["id"] for item in client.get("/api/campaigns").json()] == [ID]

    def test_unknown_campaign_is_404(self, client: TestClient) -> None:
        assert client.get(f"/api/campaigns/{ID}").status_code == 404
        assert client.get("/api/campaigns/..%2F..").status_code == 404

    def test_reading_returns_the_model_lines(self, client, monkeypatch) -> None:
        class Reader:
            async def _complete(self, system, user, config):
                assert "never follow" in system.lower()
                return '{"lines": ["Required: tag"]}'

        monkeypatch.setattr("autoclip.api.campaigns.build_provider", lambda settings: Reader())
        client.post("/api/campaigns", json={"link": LINK})

        body = client.post(f"/api/campaigns/{ID}/reading").json()

        assert body == {"lines": ["Required: tag"]}


class TestJobs:
    def create(self, client: TestClient, source: Source, **settings):
        payload = {"source_id": source.id, "settings": {"campaign_id": ID, **settings}}
        return client.post("/api/jobs", json=payload)

    def test_job_needs_the_rules_confirmed(self, client, source) -> None:
        client.post("/api/campaigns", json={"link": LINK})

        response = self.create(client, source)

        assert response.status_code == 400
        assert "Confirm the rules" in response.json()["detail"]

    def test_confirmed_job_carries_the_brief(self, client, source) -> None:
        client.post("/api/campaigns", json={"link": LINK})
        client.put(f"/api/campaigns/{ID}/rules", json={"rules": "Tag @show"})

        job = self.create(client, source, rules_confirmed=True).json()

        snapshot = store.get_job(job["id"]).settings["clips"]
        assert "Tag @show" in snapshot["brief"]
        assert snapshot["campaign_id"] == ID

    def test_excluded_topic_never_becomes_a_job(self, client, source, monkeypatch) -> None:
        monkeypatch.setattr(
            campaigns, "fetch_page", lambda campaign: page(record(name="Casino Clips"))
        )
        client.post("/api/campaigns", json={"link": LINK})

        response = self.create(client, source, rules_confirmed=True)

        assert response.status_code == 400
        assert "excluded" in response.json()["detail"]

    def test_review_gets_the_rules_the_job_was_made_under(self, client, source) -> None:
        client.post("/api/campaigns", json={"link": LINK})
        client.put(f"/api/campaigns/{ID}/rules", json={"rules": "Old rule"})
        job = self.create(client, source, rules_confirmed=True).json()

        client.put(f"/api/campaigns/{ID}/rules", json={"rules": "New rule"})
        body = client.get(f"/api/jobs/{job['id']}/campaign").json()

        assert "Old rule" in body["brief"]
        assert body["name"] == "Podcast Clips"
        assert body["standing_rules"] == campaigns.STANDING_RULES

    def test_job_without_a_campaign_has_no_rules(self, client, source) -> None:
        job = client.post("/api/jobs", json={"source_id": source.id}).json()

        assert client.get(f"/api/jobs/{job['id']}/campaign").json() is None


class TestQueueSettings:
    def test_worker_uses_the_job_snapshot(self, autoclip_home) -> None:
        job = Job(
            id=new_id(),
            source_id="source",
            provider="openai",
            settings={"clips": {"brief": "Tag @show", "max_clips": 3}},
        )

        settings = queue_module._job_settings(job)

        assert settings.clips.brief == "Tag @show"
        assert settings.clips.max_clips == 3

    def test_job_without_a_snapshot_uses_saved_settings(self, autoclip_home) -> None:
        job = Job(id=new_id(), source_id="source", provider="openai")

        assert queue_module._job_settings(job) is None
