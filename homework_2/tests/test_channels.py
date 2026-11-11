"""Роль 3: свой RSS-канал. Проверяется хранение, валидность RSS и чтение канала агрегатором."""
from xml.etree import ElementTree as ET

import httpx
import pytest

from app.aggregator import fetch_feed, parse_feed
from app.channels import ChannelError, ChannelStore, channel_feed_url

BASE = "http://localhost:8000"


@pytest.fixture
def store(tmp_path):
    return ChannelStore(tmp_path / "news.db")


def test_create_and_post(store):
    ch = store.create_channel("  Тестовый канал ", "описание")
    assert ch.id and ch.title == "Тестовый канал"
    post = store.post_article(ch.id, "ЦБ снизил ставку", "Текст", "https://example.org/a")
    assert post.id and post.channel_id == ch.id and post.link == "https://example.org/a"
    assert [p.title for p in store.list_posts(ch.id)] == ["ЦБ снизил ставку"]
    assert store.list_channels() == [ch]


def test_posts_newest_first(store):
    ch = store.create_channel("К")
    for t in ["первый", "второй", "третий"]:
        store.post_article(ch.id, t)
    assert [p.title for p in store.list_posts(ch.id)] == ["третий", "второй", "первый"]
    items = ET.fromstring(store.render_rss(ch.id, BASE).encode()).findall("channel/item")
    assert [i.findtext("title") for i in items] == ["третий", "второй", "первый"]


def test_survives_restart(tmp_path):
    first = ChannelStore(tmp_path / "news.db")
    ch = first.create_channel("Канал")
    first.post_article(ch.id, "Пост")
    second = ChannelStore(tmp_path / "news.db")           # «перезапуск сервиса»
    assert second.get_channel(ch.id).title == "Канал"
    assert [p.title for p in second.list_posts(ch.id)] == ["Пост"]


@pytest.mark.parametrize("title,text,link", [
    ("", "текст", None), ("   ", "", None), ("x" * 301, "", None),
    ("ок", "x" * 20_001, None), ("ок", "", "javascript:alert(1)"), ("ок", "", "example.org"),
])
def test_invalid_post(store, title, text, link):
    ch = store.create_channel("К")
    with pytest.raises(ChannelError):
        store.post_article(ch.id, title, text, link)
    assert store.list_posts(ch.id) == []


def test_unknown_channel(store):
    with pytest.raises(ChannelError):
        store.post_article(999, "заголовок")
    with pytest.raises(ChannelError):
        store.render_rss(999, BASE)
    assert store.get_channel(999) is None


def test_empty_channel_title(store):
    with pytest.raises(ChannelError):
        store.create_channel("  ")


def test_delete_post(store):
    ch = store.create_channel("К")
    p = store.post_article(ch.id, "ошибка")
    assert store.delete_post(ch.id, p.id) and not store.delete_post(ch.id, p.id)
    assert store.list_posts(ch.id) == []


def test_rss_is_valid_rss2(store):
    ch = store.create_channel("Канал & <тест>")
    store.post_article(ch.id, 'Заголовок "в кавычках" & <script>', "Текст <b>с</b> разметкой & амперсандом")
    root = ET.fromstring(store.render_rss(ch.id, BASE + "/").encode("utf-8"))
    assert root.tag == "rss" and root.get("version") == "2.0"
    channel = root.find("channel")
    assert channel.findtext("title") == "Канал & <тест>"
    for tag in ("link", "description", "lastBuildDate"):
        assert channel.findtext(tag)
    atom_self = channel.find("{http://www.w3.org/2005/Atom}link")
    assert atom_self.get("href") == f"{BASE}/rss/{ch.id}.xml" and atom_self.get("rel") == "self"
    item = channel.find("item")
    assert item.findtext("title") == 'Заголовок "в кавычках" & <script>'
    assert item.find("guid").get("isPermaLink") == "false"
    assert item.findtext("pubDate").endswith("GMT")


def test_empty_channel_renders(store):
    ch = store.create_channel("Пустой")
    assert parse_feed(store.render_rss(ch.id, BASE), channel_feed_url(BASE, ch.id)).articles == []


def test_aggregator_reads_own_channel(store):
    """Свой канал читается тем же кодом, что и чужой, — без HTTP и через HTTP."""
    ch = store.create_channel("Канал")
    post = store.post_article(ch.id, "Нефть подорожала", "Цены на нефть выросли")
    url = channel_feed_url(BASE, ch.id)

    (art,) = parse_feed(store.render_rss(ch.id, BASE), url, feed_id=5).articles
    assert (art.title, art.summary, art.feed_id) == ("Нефть подорожала", "Цены на нефть выросли", 5)
    assert art.guid == f"channel-{ch.id}-post-{post.id}"
    assert art.published_at == post.published_at
    assert art.link == f"{url}#post-{post.id}"

    def handler(request):
        cid = int(request.url.path.removeprefix("/rss/").removesuffix(".xml"))
        return httpx.Response(200, text=store.render_rss(cid, BASE),
                              headers={"content-type": "application/rss+xml; charset=utf-8"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    assert [a.guid for a in fetch_feed(url, client=client)] == [art.guid]


def test_new_post_gets_new_guid(store):
    ch = store.create_channel("Канал")
    store.post_article(ch.id, "один")
    before = {a.guid for a in parse_feed(store.render_rss(ch.id, BASE), "u").articles}
    store.post_article(ch.id, "два")
    after = {a.guid for a in parse_feed(store.render_rss(ch.id, BASE), "u").articles}
    assert before < after and len(after - before) == 1
