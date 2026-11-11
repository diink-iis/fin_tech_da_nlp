"""Роль 3: чтение лент. Сеть не нужна — HTTP подменяется httpx.MockTransport."""
import asyncio
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app.aggregator import (
    FeedError, Poller, fetch_feed, html_to_text, normalize_url, parse_feed, read_feed, refresh_feeds,
)

RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <title>Тестовая лента</title><link>https://news.example/</link><description>d</description>
  <item>
    <title>Банки снизили ставки</title>
    <link>/news/1</link>
    <guid>news-1</guid>
    <description><![CDATA[<p>Первый&nbsp;абзац</p><script>alert(1)</script><p>Второй <b>абзац</b> &amp; ещё</p>]]></description>
    <pubDate>Tue, 10 Nov 2026 12:30:00 +0300</pubDate>
  </item>
  <item>
    <title>Без guid</title><link>https://news.example/news/2</link>
    <description>текст</description><pubDate>Tue, 10 Nov 2026 10:00:00 GMT</pubDate>
  </item>
  <item><title>Дубль</title><guid>news-1</guid><description>повтор</description></item>
  <item><title>Ни guid, ни ссылки</title><description>только текст</description></item>
</channel></rss>"""

ATOM = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Atom-лента</title><link href="https://atom.example/"/>
  <id>urn:feed</id><updated>2026-11-10T09:00:00Z</updated>
  <entry>
    <title>Нефть подорожала</title><link href="https://atom.example/1"/>
    <id>urn:entry:1</id><updated>2026-11-10T09:00:00Z</updated>
    <content type="html">&lt;p&gt;Цены на &lt;b&gt;нефть&lt;/b&gt; выросли&lt;/p&gt;</content>
  </entry>
</feed>"""

HTML_PAGE = """<!doctype html><html><head><title>Сайт</title>
<link rel="alternate" type="application/rss+xml" title="RSS" href="/rss.xml">
</head><body>Главная</body></html>"""


def transport(routes):
    """routes: путь → (статус, тело, content-type)."""
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path not in routes:
            return httpx.Response(404, text="not found")
        status, body, ctype = routes[request.url.path]
        if isinstance(body, Exception):
            raise body
        return httpx.Response(status, content=body if isinstance(body, bytes) else body.encode("utf-8"),
                              headers={"content-type": ctype})
    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)


# ---------------------------------------------------------------- разбор
class TestParse:
    def test_fields(self):
        info = parse_feed(RSS, "https://news.example/rss", feed_id=7)
        assert info.title == "Тестовая лента"
        first = info.articles[0]
        assert (first.id, first.feed_id, first.guid) == (0, 7, "news-1")
        assert first.title == "Банки снизили ставки"
        assert first.link == "https://news.example/news/1"          # относительная ссылка раскрыта
        assert first.published_at == datetime(2026, 11, 10, 9, 30, tzinfo=timezone.utc)

    def test_html_removed_from_summary(self):
        summary = parse_feed(RSS, "https://news.example/rss").articles[0].summary
        assert summary == "Первый абзац\nВторой абзац & ещё"
        assert "<" not in summary and "alert" not in summary

    def test_guid_fallbacks(self):
        arts = parse_feed(RSS, "https://news.example/rss").articles
        assert arts[1].guid == "https://news.example/news/2"        # нет guid — ссылка
        assert arts[2].guid.startswith("sha1:")                     # нет ничего — хеш
        again = parse_feed(RSS, "https://news.example/rss").articles
        assert arts[2].guid == again[2].guid                        # хеш устойчив между опросами

    def test_duplicates_dropped(self):
        titles = [a.title for a in parse_feed(RSS, "https://news.example/rss").articles]
        assert "Дубль" not in titles and len(titles) == 3

    def test_missing_date_gets_fetch_time(self):
        art = parse_feed(RSS, "https://news.example/rss").articles[2]
        assert abs(datetime.now(timezone.utc) - art.published_at) < timedelta(minutes=1)

    def test_atom(self):
        info = parse_feed(ATOM, "https://atom.example/feed")
        (art,) = info.articles
        assert (info.title, art.guid, art.link) == ("Atom-лента", "urn:entry:1", "https://atom.example/1")
        assert art.summary == "Цены на нефть выросли"
        assert art.published_at == datetime(2026, 11, 10, 9, 0, tzinfo=timezone.utc)

    def test_windows_1251(self):
        body = RSS.replace('encoding="UTF-8"', 'encoding="windows-1251"').encode("cp1251")
        info = parse_feed(body, "https://news.example/rss")
        assert info.articles[0].title == "Банки снизили ставки"

    def test_empty_feed_is_not_an_error(self):
        info = parse_feed('<rss version="2.0"><channel><title>Пусто</title></channel></rss>', "u")
        assert info.articles == [] and info.title == "Пусто"

    @pytest.mark.parametrize("body", ["", "просто текст", HTML_PAGE, "<html><body>нет</body></html>"])
    def test_not_a_feed(self, body):
        with pytest.raises(FeedError):
            parse_feed(body, "https://x.example/")

    def test_html_to_text(self):
        assert html_to_text("a<br>b &lt;c&gt;") == "a\nb <c>"
        assert html_to_text(None) == ""


# ---------------------------------------------------------------- сеть
class TestRead:
    def test_fetch_feed_contract(self):
        client = transport({"/rss": (200, RSS, "application/rss+xml")})
        arts = fetch_feed("https://news.example/rss", feed_id=3, client=client)
        assert len(arts) == 3 and all(a.feed_id == 3 for a in arts)

    def test_discovers_feed_on_html_page(self):
        client = transport({"/": (200, HTML_PAGE, "text/html; charset=utf-8"),
                            "/rss.xml": (200, RSS, "application/rss+xml")})
        info = read_feed("https://news.example/", client=client)
        assert info.url == "https://news.example/rss.xml" and len(info.articles) == 3

    def test_html_page_without_feed(self):
        client = transport({"/": (200, "<html><body>сайт</body></html>", "text/html")})
        with pytest.raises(FeedError, match="RSS"):
            read_feed("https://news.example/", client=client)

    def test_http_error(self):
        with pytest.raises(FeedError, match="404"):
            read_feed("https://news.example/missing", client=transport({}))

    def test_network_error(self):
        client = transport({"/rss": (200, httpx.ConnectError("нет сети"), "")})
        with pytest.raises(FeedError):
            read_feed("https://news.example/rss", client=client)

    def test_timeout(self):
        client = transport({"/rss": (200, httpx.ReadTimeout("долго"), "")})
        with pytest.raises(FeedError, match="время"):
            read_feed("https://news.example/rss", client=client)

    def test_too_big(self, monkeypatch):
        monkeypatch.setattr("app.aggregator.MAX_FEED_BYTES", 100)
        client = transport({"/rss": (200, RSS, "application/rss+xml")})
        with pytest.raises(FeedError, match="больше"):
            read_feed("https://news.example/rss", client=client)

    def test_normalize_url(self):
        assert normalize_url("  lenta.ru/rss ") == "https://lenta.ru/rss"
        assert normalize_url("http://a.example/x") == "http://a.example/x"
        for bad in ["", "   ", "ftp://a.example/", "file:///etc/passwd", "https://"]:
            with pytest.raises(FeedError):
                normalize_url(bad)


# ---------------------------------------------------------------- опрос
def test_refresh_feeds_isolates_failures():
    client = transport({"/ok": (200, RSS, "application/rss+xml"), "/atom": (200, ATOM, "application/atom+xml")})
    saved = {}

    def save(feed_id, articles):
        saved[feed_id] = articles
        return len(articles) - 1

    results = refresh_feeds([(1, "https://a.example/ok"), (2, "https://a.example/broken"),
                             (3, "https://a.example/atom")], save, client=client)
    by_id = {r.feed_id: r for r in results}
    assert (by_id[1].fetched, by_id[1].new, by_id[1].error) == (3, 2, None)
    assert by_id[2].error and 2 not in saved
    assert by_id[3].fetched == 1 and saved[3][0].feed_id == 3


def test_refresh_feeds_save_error_is_reported():
    client = transport({"/ok": (200, RSS, "application/rss+xml")})

    def save(feed_id, articles):
        raise RuntimeError("диск заполнен")

    (r,) = refresh_feeds([(1, "https://a.example/ok")], save, client=client)
    assert "диск заполнен" in r.error


def test_refresh_feeds_empty():
    assert refresh_feeds([], lambda *_: 0) == []


def test_poller_refresh_now_and_background_loop():
    client = transport({"/ok": (200, RSS, "application/rss+xml")})
    feeds = [(1, "https://a.example/ok")]
    calls = []

    def save(feed_id, articles):
        calls.append(feed_id)
        return 0

    async def scenario():
        poller = Poller(lambda: feeds, save, interval=0.05, client=client)
        results = await poller.refresh_now()
        assert results[0].fetched == 3 and len(calls) == 1
        feeds.append((2, "https://a.example/ok"))       # новый канал подхватывается без перезапуска
        poller.start()
        await asyncio.sleep(0.3)
        await poller.stop()
        return poller

    poller = asyncio.run(scenario())
    assert 2 in calls and len(calls) >= 3
    assert {r.feed_id for r in poller.last_results} == {1, 2}
