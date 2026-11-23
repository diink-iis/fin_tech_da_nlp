"""Integration regressions: validation, batched vectors and local RSS identity."""
from datetime import datetime, timezone
import sqlite3

import httpx
import pytest
from fastapi.testclient import TestClient

from app.channels import ChannelStore
from app.contracts import Article, MatchReason
from app.db import Repository
from app.engines.semantic import SemanticEngine
from app.main import create_app
from tests.test_semantic import CountingEncoder


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(str(tmp_path / "news.db"), polling=False)) as c:
        yield c


def user(client, name="Аня"):
    response = client.post("/api/users", json={"name": name})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def channel(client, uid):
    response = client.post("/api/channels", json={"title": "Демо", "user_id": uid})
    assert response.status_code == 201, response.text
    return response.json()


@pytest.mark.parametrize("payload", [
    {"name": " ", "kind": "keyword", "params": {"any": ["нефть"]}},
    {"name": "empty", "kind": "keyword", "params": {"any": [" "]}},
    {"name": "empty", "kind": "keyword", "params": {"any": ["!!!"]}},
    {"name": "empty", "kind": "topic", "params": {"description": "<p></p>"}},
    {"name": "hidden", "kind": "topic", "params": {"examples": ["<script>спорт</script>"]}},
    {"name": "boolean", "kind": "topic", "params": {"description": "спорт", "threshold": True}},
    {"name": "bad", "kind": "topic", "params": {"description": "спорт", "aggregate": "median"}},
])
def test_invalid_filters_are_rejected_before_storage(client, payload):
    uid = user(client)
    response = client.post(f"/api/users/{uid}/filters", json=payload)
    assert response.status_code == 422, response.text
    assert client.get(f"/api/users/{uid}/filters").json() == []


def test_filter_normalization_and_invalid_update_preserves_original(client):
    uid = user(client)
    response = client.post(f"/api/users/{uid}/filters", json={
        "name": "  Нефть  ", "kind": "keyword",
        "params": {"any": "нефть, газ;  ", "fields": "title"},
    })
    assert response.status_code == 201, response.text
    saved = response.json()
    assert saved["name"] == "Нефть"
    assert saved["params"]["any"] == ["нефть", "газ"]
    assert saved["params"]["fields"] == ["title"]
    bad = client.put(f"/api/users/{uid}/filters/{saved['id']}", json={
        "name": "bad", "kind": "topic", "params": {"description": "спорт", "threshold": True},
    })
    assert bad.status_code == 422
    assert client.get(f"/api/users/{uid}/filters").json()[0] == saved
    topic = client.post(f"/api/users/{uid}/filters", json={
        "name": "  Спорт ", "kind": "topic",
        "params": {"description": "<p>Спорт&nbsp;и матчи</p>", "examples": "<b>Футбол</b>", "threshold": "0.4"},
    })
    assert topic.status_code == 201, topic.text
    assert topic.json()["params"] == {
        "description": "Спорт и матчи", "examples": ["Футбол"], "threshold": 0.4, "aggregate": "mean",
    }


def test_local_channel_keeps_feed_and_owner_across_host_change_and_restart(tmp_path):
    path = str(tmp_path / "identity.db")
    with TestClient(create_app(path, polling=False), base_url="http://localhost:8000") as c:
        uid = user(c)
        ch = channel(c, uid)
        c.post(f"/api/users/{uid}/filters", json={"name": "Нефть", "kind": "keyword", "params": {"any": ["нефть"]}})
        posted = c.post(f"http://127.0.0.1:8888/api/channels/{ch['id']}/articles", json={"title": "Нефть подорожала"})
        assert posted.status_code == 201, posted.text
        assert posted.json()["feed_id"] == ch["feed_id"]
        assert len(c.get("/api/feeds").json()) == 1
        assert len(c.get(f"/api/users/{uid}/news").json()) == 1
        owner = c.get("/api/channels").json()[0]
        assert owner["owner_user_id"] == uid and owner["feed_id"] == ch["feed_id"]
        uid2 = user(c, "Борис")
        subscribed = c.post(f"http://127.0.0.1:8888/api/users/{uid2}/feeds", json={"url": f"http://127.0.0.1:8888/rss/{ch['id']}.xml"})
        assert subscribed.status_code == 201, subscribed.text
        assert subscribed.json()["id"] == ch["feed_id"]
    with TestClient(create_app(path, polling=False), base_url="http://127.0.0.1:9000") as restarted:
        assert restarted.get("/api/channels").json()[0]["owner_user_id"] == uid
        posted = restarted.post(f"/api/channels/{ch['id']}/articles", json={"title": "Нефть снова выросла"})
        assert posted.json()["feed_id"] == ch["feed_id"]
        assert len(restarted.get(f"/api/users/{uid}/news").json()) == 2


def test_refresh_reads_own_rss_without_network_and_deduplicates(client):
    uid = user(client)
    ch = channel(client, uid)
    client.post(f"/api/channels/{ch['id']}/articles", json={"title": "Первая новость"})
    calls = []

    def network_must_not_be_used(request):
        calls.append(str(request.url))
        raise AssertionError("Own RSS should be parsed in-process")

    with httpx.Client(transport=httpx.MockTransport(network_must_not_be_used)) as transport:
        client.app.state.poller.client = transport
        client.app.state.channels.post_article(ch["id"], "Новая запись")
        first = client.post("/api/refresh").json()
        second = client.post("/api/refresh").json()
    assert first["new"] == 1 and first["errors"] == 0, first
    assert second["new"] == 0 and second["errors"] == 0, second
    assert calls == []


def test_legacy_local_feed_is_recovered_without_moving_data(tmp_path):
    path = str(tmp_path / "legacy.db")
    repo = Repository(path)
    channels = ChannelStore(path)
    uid = repo.create_user("Legacy")["id"]
    ch = channels.create_channel("Legacy channel")
    feed = repo.add_feed(f"http://localhost:1234/rss/{ch.id}.xml", ch.title)
    repo.subscribe(uid, feed["id"])
    channels.post_article(ch.id, "Нефть выросла")
    with sqlite3.connect(path) as conn:
        conn.execute("DROP TABLE channel_feeds")
        conn.execute("CREATE TABLE channel_feeds(channel_id INTEGER PRIMARY KEY, feed_id INTEGER NOT NULL UNIQUE REFERENCES feeds(id))")
    with TestClient(create_app(path, polling=False)) as c:
        meta = c.get("/api/channels").json()[0]
        assert meta["feed_id"] == feed["id"] and meta["owner_user_id"] is None
        assert c.post("/api/refresh").json()["new"] == 1
        assert c.get(f"/api/users/{uid}/feeds").json()[0]["id"] == feed["id"]


def test_semantic_candidates_are_batched_and_reused_after_restart(tmp_path):
    path = str(tmp_path / "batch.db")
    encoder = CountingEncoder()
    engine = SemanticEngine("review-fake", encoder=encoder)
    with TestClient(create_app(path, polling=False, semantic_engine=engine)) as c:
        uid = user(c)
        ch = channel(c, uid)
        for title in ["Первая новость", "Вторая новость", "Третья новость"]:
            c.post(f"/api/channels/{ch['id']}/articles", json={"title": title})
        for description in ["спорт", "экономика"]:
            assert c.post(f"/api/users/{uid}/filters", json={"name": description, "kind": "topic", "params": {"description": description, "threshold": 0}}).status_code == 201
        first = c.get(f"/api/users/{uid}/news")
        assert first.status_code == 200 and len(first.json()) == 3
        assert len(encoder.calls[0]) == 3
        assert encoder.texts_encoded == 5  # three articles and two topic descriptions
        assert c.get(f"/api/users/{uid}/news").json() == first.json()
        assert encoder.texts_encoded == 5
    after = CountingEncoder()
    with TestClient(create_app(path, polling=False, semantic_engine=SemanticEngine("review-fake", encoder=after))) as c:
        assert c.get(f"/api/users/{uid}/news").json() == first.json()
        assert after.texts_encoded == 2  # only the two topics


def test_keyword_news_never_calls_semantic_engine(tmp_path):
    class NotUsed:
        def index(self, articles):
            raise AssertionError("keyword-only search must not load semantic model")

        def match(self, article, flt):
            raise AssertionError("keyword-only search must not load semantic model")

    with TestClient(create_app(str(tmp_path / "keyword.db"), polling=False, semantic_engine=NotUsed())) as c:
        uid = user(c)
        ch = channel(c, uid)
        c.post(f"/api/users/{uid}/filters", json={"name": "Нефть", "kind": "keyword", "params": {"any": ["нефть"]}})
        c.post(f"/api/channels/{ch['id']}/articles", json={"title": "Нефть выросла"})
        assert len(c.get(f"/api/users/{uid}/news").json()) == 1


def test_match_only_semantic_engine_remains_injectable(tmp_path):
    class MatchOnly:
        def match(self, article, flt):
            return MatchReason(flt.id, flt.name, "topic", {"score": 1.0, "threshold": 0.45})

    with TestClient(create_app(str(tmp_path / "stub.db"), polling=False, semantic_engine=MatchOnly())) as c:
        uid = user(c)
        ch = channel(c, uid)
        c.post(f"/api/users/{uid}/filters", json={"name": "topic", "kind": "topic", "params": {"description": "спорт"}})
        c.post(f"/api/channels/{ch['id']}/articles", json={"title": "Матч"})
        response = c.get(f"/api/users/{uid}/news")
        assert response.status_code == 200 and len(response.json()) == 1


def test_legacy_recovery_does_not_claim_external_rss_with_same_path(tmp_path):
    path = str(tmp_path / "external.db")
    repo = Repository(path)
    ch = ChannelStore(path).create_channel("Демо")
    external = repo.add_feed(f"https://publisher.example/rss/{ch.id}.xml", ch.title)
    own = repo.ensure_channel_feed(ch.id, ch.title, f"http://localhost:8000/rss/{ch.id}.xml")
    assert own["id"] != external["id"]
    assert repo.local_channel_for_feed(external["id"]) is None


def test_history_is_not_silently_limited_to_ten_thousand_candidates(tmp_path, monkeypatch):
    with TestClient(create_app(str(tmp_path / "history.db"), polling=False)) as c:
        uid = user(c)
        ch = channel(c, uid)
        c.post(f"/api/users/{uid}/filters", json={"name": "needle", "kind": "keyword", "params": {"any": ["needle"]}})
        repo = c.app.state.repository
        articles = [Article(0, ch["feed_id"], "needle", "needle", "", "", datetime(2010, 1, 1, tzinfo=timezone.utc))]
        articles += [Article(0, ch["feed_id"], str(i), "other", "", "", datetime(2020, 1, 1, tzinfo=timezone.utc)) for i in range(10000)]
        repo.save_articles(ch["feed_id"], articles)
        # Match persistence is independent bookkeeping; avoid 10k separate write transactions here.
        monkeypatch.setattr(repo, "save_matches", lambda *args: None)
        result = c.get(f"/api/users/{uid}/news")
        assert result.status_code == 200 and len(result.json()) == 1
        assert result.json()[0]["guid"] == "needle"
