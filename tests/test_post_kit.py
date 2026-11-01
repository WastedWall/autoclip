"""Paid disclosure, research handling, post kits and the record of posts."""

from __future__ import annotations

import asyncio
import io
import json
from collections.abc import Iterator
from urllib.error import HTTPError

import pytest
from autoclip import app as app_module
from autoclip import config, postkit, research
from autoclip.app import create_app
from autoclip.config import WhisperSettings
from autoclip.db import store
from autoclip.db.models import Clip, Export, Job, Source, new_id
from autoclip.pipeline import captions, transcribe
from autoclip.pipeline.export import disclosure_label
from autoclip.pipeline.transcript import Word
from fastapi.testclient import TestClient

BRIEF_CLIPS = {"clips": {"brief": "Rule one", "disclosure_brand": "Acme", "required_tags": "@acme"}}


class FakeProvider:
    def __init__(self, answer: str) -> None:
        self.answer = answer
        self.prompts: list[str] = []

    async def _complete(self, system: str, user: str, config) -> str:
        self.prompts.append(user)
        return self.answer


@pytest.fixture
def client(autoclip_home, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv(app_module.ENV_NO_WORKER, "1")
    with TestClient(create_app()) as test_client:
        yield test_client


@pytest.fixture
def clip(client: TestClient) -> Clip:
    source = store.create_source(
        Source(id=new_id(), type="upload", path="C:/video.mp4", title="Test", duration_s=60.0)
    )
    job = store.create_job(
        Job(id=new_id(), source_id=source.id, provider="openai", settings=BRIEF_CLIPS)
    )
    clip = Clip(id=new_id(), job_id=job.id, start_s=0.0, end_s=30.0, title="A clip", rank=1)
    store.replace_clips(job.id, [clip])
    return clip


class TestWhisper:
    def test_carry_over_is_off_by_default(self) -> None:
        assert WhisperSettings().condition_on_previous_text is False

    def test_setting_reaches_the_model(self, monkeypatch, tmp_path) -> None:
        seen = {}

        class Model:
            def __init__(self, *args, **kwargs) -> None:
                pass

            def transcribe(self, audio, **kwargs):
                seen.update(kwargs)
                segment = type(
                    "Segment",
                    (),
                    {
                        "text": "hi",
                        "start": 0.0,
                        "end": 1.0,
                        "words": [type("W", (), {"word": "hi", "start": 0.0, "end": 1.0})()],
                    },
                )()
                return [segment], type("Info", (), {"language": "en", "duration": 1.0})()

        monkeypatch.setattr("faster_whisper.WhisperModel", Model)
        monkeypatch.setattr(transcribe, "resolve_compute", lambda settings: ("cpu", "int8"))

        transcribe.transcribe(tmp_path / "audio.wav", WhisperSettings())

        assert seen["condition_on_previous_text"] is False


class TestDisclosure:
    WORDS = [Word(text="hello", start=0.5, end=1.0)]

    def build(self, disclosure: str, duration_s: float):
        style = captions.get_style("bold_pop")
        return captions.build_ass(
            self.WORDS, style, width=1080, height=1920, disclosure=disclosure, duration_s=duration_s
        )

    def events(self, subs):
        return [e for e in subs.events if e.style == captions.DISCLOSURE_STYLE_NAME]

    def test_label_names_the_brand(self) -> None:
        assert disclosure_label("Acme") == "Paid partnership with Acme"
        assert disclosure_label("") == ""

    def test_full_label_then_small_ad_until_the_end(self) -> None:
        first, second = self.events(self.build("Paid partnership with Acme", 30.0))

        assert first.text == "Paid partnership with Acme"
        assert (first.start, first.end) == (0, 3000)
        assert second.text == "#Ad"
        assert (second.start, second.end) == (3000, 30000)

    def test_short_clip_keeps_the_full_label(self) -> None:
        events = self.events(self.build("Paid partnership with Acme", 2.0))

        assert len(events) == 1
        assert events[0].end == 2000

    def test_no_disclosure_without_a_brand(self) -> None:
        assert self.events(self.build("", 30.0)) == []

    def test_ass_markup_in_a_brand_is_removed(self) -> None:
        first, _ = self.events(self.build("Paid partnership with {\\b1}Acme", 30.0))

        assert first.text == "Paid partnership with b1Acme"


class TestCaption:
    def test_disclosure_comes_first_and_tags_last(self) -> None:
        caption = postkit.build_caption(
            "A real lesson.", brand="Acme", keywords="learn English, tips", required_tags="@acme"
        )

        assert caption.startswith("#Ad Paid partnership with Acme.")
        assert caption.endswith("#learnEnglish #tips @acme")

    def test_no_disclosure_without_a_brand(self) -> None:
        assert postkit.build_caption("Text", brand="", keywords="", required_tags="") == "Text"

    def test_keeps_hashtags_few(self) -> None:
        assert len(postkit.hashtags("a, b, c, d, e, f")) == postkit.MAX_HASHTAGS

    def test_repeated_keywords_make_one_hashtag(self) -> None:
        assert postkit.hashtags("ai, ai, video") == ["#ai", "#video"]


class TestWriteKit:
    ANSWER = json.dumps(
        {
            "title": "x" * 200,
            "text": "What the clip says.",
            "category": "Education",
            "language": "English",
            "flags": ["Check the tag"],
        }
    )

    async def write(self, provider, **overrides):
        values = {
            "clip_text": "the clip words",
            "brief": "Rule one",
            "report": "The report",
            "approach": "search",
            "keywords": "learn English",
            "brand": "Acme",
            "required_tags": "@acme",
        }
        return await postkit.write_kit(provider, **(values | overrides))

    async def test_builds_the_kit_in_code(self) -> None:
        kit = await self.write(FakeProvider(self.ANSWER))

        assert len(kit.title) == postkit.MAX_TITLE_CHARS
        assert kit.caption.startswith("#Ad Paid partnership with Acme.")
        assert "@acme" in kit.caption
        assert kit.flags == ["Check the tag"]

    async def test_model_gets_the_choice_and_the_report(self) -> None:
        provider = FakeProvider(self.ANSWER)

        await self.write(provider)

        assert "Approach: search" in provider.prompts[0]
        assert "Keywords: learn English" in provider.prompts[0]
        assert "The report" in provider.prompts[0]

    async def test_unusable_answer_is_a_clear_error(self) -> None:
        with pytest.raises(postkit.ProviderError):
            await self.write(FakeProvider("no json here"))


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args) -> None:
        return None


class TestResearch:
    def stub(self, monkeypatch, *answers) -> list:
        pending = list(answers)
        requests = []

        def urlopen(request, timeout):
            requests.append(request)
            return Response(json.dumps(pending.pop(0)).encode())

        monkeypatch.setattr(research.urllib.request, "urlopen", urlopen)
        return requests

    def report_answer(self, text: str = "Report", notes: list | None = None) -> dict:
        part = {"type": "output_text", "text": text, "annotations": notes or []}
        return {
            "output": [
                {"type": "web_search_call", "action": {"type": "search"}},
                {"type": "message", "content": [part]},
            ]
        }

    def test_openai_report_keeps_text_and_citations(self, monkeypatch) -> None:
        note = {
            "type": "url_citation",
            "url": "https://a.example",
            "title": "A",
            "start_index": 0,
            "end_index": 6,
        }
        requests = self.stub(monkeypatch, self.report_answer("Report details", [note]))

        report = research.openai_report("learning English", "secret")

        assert report.text == "Report details"
        assert report.citations == [research.Citation("A", "https://a.example", 0, 6)]
        body = json.loads(requests[0].data)
        assert "learning English" in body["input"]
        assert body["tools"] == [{"type": "web_search"}]
        assert body["max_tool_calls"] == research.MAX_SEARCHES
        assert body["reasoning"] == {"effort": "low"}
        assert "secret" not in requests[0].full_url

    def test_empty_citation_title_falls_back_to_the_link(self, monkeypatch) -> None:
        note = {"type": "url_citation", "url": "https://a.example", "title": "", "end_index": 3}
        self.stub(monkeypatch, self.report_answer("Report", [note]))

        assert research.openai_report("topic", "secret").citations[0].title == "https://a.example"

    def test_incomplete_youtube_items_are_skipped(self, monkeypatch) -> None:
        self.stub(monkeypatch, {"items": [{"id": {"videoId": "a"}}]})

        assert research.youtube_results("topic", "secret") == []

    def test_empty_report_is_an_error(self, monkeypatch) -> None:
        self.stub(monkeypatch, {"output": []})

        with pytest.raises(research.ResearchError):
            research.openai_report("topic", "secret")

    def test_youtube_keeps_titles_and_order(self, monkeypatch) -> None:
        search = {
            "items": [
                {
                    "id": {"videoId": "a"},
                    "snippet": {"title": "It&#39;s one", "channelTitle": "Chan"},
                },
                {"id": {"videoId": "b"}, "snippet": {"title": "Two", "channelTitle": "Chan"}},
            ]
        }
        stats = {"items": [{"id": "a", "statistics": {"viewCount": "12"}}]}
        requests = self.stub(monkeypatch, search, stats)

        results = research.youtube_results("topic", "secret")

        assert [r.title for r in results] == ["It's one", "Two"]
        assert results[0].views == 12
        assert results[1].views == 0
        assert all("secret" not in request.full_url for request in requests)

    def test_refused_request_never_shows_the_key(self, monkeypatch) -> None:
        def urlopen(request, timeout):
            raise HTTPError(request.full_url, 403, "Forbidden", {}, None)

        monkeypatch.setattr(research.urllib.request, "urlopen", urlopen)

        with pytest.raises(research.ResearchError) as error:
            research.openai_report("topic", "secret")

        assert "secret" not in str(error.value)
        assert "403" in str(error.value)


class TestYouTubeQuota:
    def test_quota_error_is_recognised(self, monkeypatch) -> None:
        def urlopen(request, timeout):
            body = io.BytesIO(b'{"error": {"errors": [{"reason": "quotaExceeded"}]}}')
            raise HTTPError(request.full_url, 403, "Forbidden", {}, body)

        monkeypatch.setattr(research.urllib.request, "urlopen", urlopen)

        with pytest.raises(research.ResearchError, match="quota"):
            research.youtube_results("topic", "secret")


class TestQuota:
    @pytest.fixture(autouse=True)
    def quota_error(self, monkeypatch) -> None:
        def urlopen(request, timeout):
            raise HTTPError(request.full_url, 429, "Too Many Requests", {}, None)

        monkeypatch.setattr(research.urllib.request, "urlopen", urlopen)

    def test_openai_points_at_the_credit(self) -> None:
        with pytest.raises(research.ResearchError, match="credit"):
            research.openai_report("topic", "secret")

    def test_youtube_does_not_mention_openai(self) -> None:
        with pytest.raises(research.ResearchError) as error:
            research.youtube_results("topic", "secret")

        assert "OpenAI" not in str(error.value)


class TestPostKitApi:
    def use_model(self, monkeypatch, answer: str) -> FakeProvider:
        provider = FakeProvider(answer)
        monkeypatch.setattr("autoclip.api.posts.build_provider", lambda settings: provider)
        return provider

    def test_write_then_read_a_kit(self, client, clip, monkeypatch) -> None:
        self.use_model(monkeypatch, TestWriteKit.ANSWER)
        monkeypatch.setattr("autoclip.api.posts.clip_words", _words)

        written = client.post(
            f"/api/clips/{clip.id}/post-kit", json={"approach": "search", "keywords": "tips"}
        ).json()

        assert written["kit"]["caption"].startswith("#Ad Paid partnership with Acme.")
        assert "@acme" in written["kit"]["caption"]
        assert client.get(f"/api/clips/{clip.id}/post-kit").json()["kit"]["approach"] == "search"

    def test_unknown_approach_is_refused(self, client, clip) -> None:
        response = client.post(f"/api/clips/{clip.id}/post-kit", json={"approach": "clickbait"})

        assert response.status_code == 422

    def test_topic_comes_from_the_local_model(self, client, clip, monkeypatch) -> None:
        self.use_model(monkeypatch, '{"topic": "learning English"}')
        monkeypatch.setattr("autoclip.api.posts.clip_words", _words)

        assert client.post(f"/api/clips/{clip.id}/topic").json() == {"topic": "learning English"}

    def test_missing_provider_key_is_a_clear_error(self, client, clip, monkeypatch) -> None:
        def no_key(settings):
            raise postkit.ProviderError("No API key is set.")

        monkeypatch.setattr("autoclip.api.posts.build_provider", no_key)
        monkeypatch.setattr("autoclip.api.posts.clip_words", _words)

        assert client.post(f"/api/clips/{clip.id}/topic").status_code == 502
        response = client.post(f"/api/clips/{clip.id}/post-kit", json={"approach": "search"})
        assert response.status_code == 502

    def test_research_without_a_key_asks_for_one(self, client, monkeypatch) -> None:
        monkeypatch.setattr(config, "get_secret", lambda name: None)

        response = client.post("/api/research/youtube", json={"topic": "learning"})

        assert response.status_code == 400
        assert "Settings" in response.json()["detail"]

    def test_report_flows_into_approaches_and_the_kit(self, client, clip, monkeypatch) -> None:
        answers = iter(
            [
                json.dumps(
                    {
                        "approaches": [
                            {"key": key, "headline": key, "keywords": ["tips"], "reason": "r"}
                            for key in postkit.APPROACHES
                        ]
                    }
                ),
                TestWriteKit.ANSWER,
            ]
        )
        provider = FakeProvider("")

        async def complete(system, user, config):
            provider.prompts.append(user)
            return next(answers)

        provider._complete = complete
        monkeypatch.setattr("autoclip.api.posts.build_provider", lambda settings: provider)
        monkeypatch.setattr("autoclip.api.posts.clip_words", _words)
        monkeypatch.setattr(config, "get_secret", lambda name: "secret")
        report = research.Report(text="Live report")
        monkeypatch.setattr(research, "openai_report", lambda topic, key: report)

        body = client.post(f"/api/clips/{clip.id}/research", json={"topic": "learning"}).json()

        assert body["report"]["text"] == "Live report"
        assert [item["key"] for item in body["approaches"]] == list(postkit.APPROACHES)
        assert "Live report" in provider.prompts[0]

        state = client.post(
            f"/api/clips/{clip.id}/post-kit", json={"approach": "search", "keywords": "tips"}
        ).json()

        assert "Live report" in provider.prompts[1]
        assert state["report"]["text"] == "Live report"
        assert state["kit"]["approach"] == "search"

    def test_research_needs_the_key(self, client, clip, monkeypatch) -> None:
        monkeypatch.setattr(config, "get_secret", lambda name: None)

        response = client.post(f"/api/clips/{clip.id}/research", json={"topic": "learning"})

        assert response.status_code == 400
        assert "Settings" in response.json()["detail"]

    def test_research_key_is_not_the_qwen_key(self) -> None:
        assert config.OPENAI_RESEARCH_KEY != "openai"

    def test_missing_approach_is_a_clear_error(self) -> None:
        answer = json.dumps({"approaches": [{"key": "search", "headline": "x"}]})

        with pytest.raises(postkit.ProviderError, match="left out"):
            asyncio.run(postkit.suggest_approaches(FakeProvider(answer), clip_text="c", report="r"))

    def test_reveal_needs_an_export(self, client, clip) -> None:
        assert client.post(f"/api/clips/{clip.id}/reveal").status_code == 409

    def test_reveal_opens_explorer(self, client, clip, tmp_path, monkeypatch) -> None:
        video = tmp_path / "clip.mp4"
        video.write_bytes(b"x")
        store.create_export(Export(id=new_id(), clip_id=clip.id, path=str(video)))
        opened = []
        monkeypatch.setattr("autoclip.api.posts.sys.platform", "win32")
        monkeypatch.setattr("autoclip.api.posts.subprocess.Popen", opened.append)

        assert client.post(f"/api/clips/{clip.id}/reveal").status_code == 204
        assert opened == [["explorer", f"/select,{video}"]]


class TestPosts:
    URL = "https://www.tiktok.com/@me/video/1"

    def post(self, client, clip, url: str = URL):
        return client.put(
            f"/api/clips/{clip.id}/posts/tiktok",
            json={"url": url, "approach": "search", "title": "T", "caption": "C"},
        )

    def test_kept_clip_shows_one_row_per_platform(self, client, clip) -> None:
        store.update_clip(clip.id, status="kept")

        rows = client.get("/api/posts").json()

        assert [row["platform"] for row in rows] == ["tiktok", "instagram", "youtube"]
        assert all(row["post"] is None for row in rows)

    def test_unkept_clip_is_not_listed(self, client, clip) -> None:
        assert client.get("/api/posts").json() == []

    def test_marking_posted_records_the_post(self, client, clip) -> None:
        body = self.post(client, clip).json()

        assert body["url"] == self.URL
        assert client.get("/api/posts").json()[0]["post"]["approach"] == "search"

    def test_posting_again_keeps_one_record(self, client, clip) -> None:
        first = self.post(client, clip).json()
        second = self.post(client, clip, self.URL + "2").json()

        assert first["id"] == second["id"]
        assert second["url"].endswith("2")

    @pytest.mark.parametrize("url", ["javascript:alert(1)", "http://a.example", "not a link"])
    def test_only_https_links_are_accepted(self, client, clip, url: str) -> None:
        assert self.post(client, clip, url).status_code == 422

    def test_unknown_platform_is_404(self, client, clip) -> None:
        response = client.put(f"/api/clips/{clip.id}/posts/myspace", json={"url": self.URL})

        assert response.status_code == 404

    def test_results_are_saved(self, client, clip) -> None:
        post = self.post(client, clip).json()

        body = client.patch(
            f"/api/posts/{post['id']}", json={"views_48h": 120, "views_7d": 900, "submitted": True}
        ).json()

        assert body["views_7d"] == 900
        assert body["submitted"] is True

    def test_negative_views_are_refused(self, client, clip) -> None:
        post = self.post(client, clip).json()

        response = client.patch(f"/api/posts/{post['id']}", json={"views_48h": -1})

        assert response.status_code == 422

    def test_csv_lists_posts(self, client, clip) -> None:
        self.post(client, clip)

        response = client.get("/api/posts.csv")

        assert response.headers["content-type"].startswith("text/csv")
        assert "A clip,tiktok,search" in response.text

    @pytest.mark.parametrize("title", ["=HYPERLINK(1)", "\t=HYPERLINK(1)"])
    def test_csv_defuses_formulas(self, client, clip, title: str) -> None:
        store.update_clip(clip.id, title=title)
        self.post(client, clip)

        assert f"'{title}" in client.get("/api/posts.csv").text


async def _words(clip_id: str) -> list:
    return [Word(text="hello", start=0.0, end=1.0)]
