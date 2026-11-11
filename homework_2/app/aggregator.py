"""Агрегатор: читает RSS/Atom-ленты и превращает записи в Article.

Модуль не знает о БД и фильтрах: на входе адрес ленты, на выходе список Article без дублей.
Сохранение (и отсев статей, которые уже есть в базе, по паре «лента, guid») — дело роли 4,
она передаёт функцию `save` в refresh_feeds / Poller.

    articles = fetch_feed("https://lenta.ru/rss", feed_id=3)       # контракт из docs/PLAN.md
    info = read_feed("lenta.ru")                                   # + название ленты и итоговый адрес
    results = refresh_feeds([(3, url)], save)                      # опросить несколько лент параллельно
    poller = Poller(get_feeds, save, interval=300); poller.start() # фоновый опрос в lifespan FastAPI

Все ошибки сети и разбора превращаются в FeedError с понятным текстом для интерфейса.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Callable, Iterable, Optional, Sequence
from urllib.parse import urljoin, urlparse

import feedparser
import httpx

from app.contracts import Article

log = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 15.0
MAX_FEED_BYTES = 5 * 1024 * 1024
MAX_SUMMARY_CHARS = 4000
USER_AGENT = "Mozilla/5.0 (compatible; fin-tech-da-nlp-rss/1.0)"

_SPACES = re.compile(r"[ \t\r\f\v ]+")
_NEWLINES = re.compile(r"\s*\n\s*")
_FEED_TYPES = ("application/rss+xml", "application/atom+xml", "application/xml", "text/xml")
_BLOCK_TAGS = frozenset({
    "address", "article", "blockquote", "br", "div", "h1", "h2", "h3", "h4", "h5", "h6",
    "hr", "li", "ol", "p", "section", "table", "td", "th", "tr", "ul",
})


class FeedError(RuntimeError):
    """Ленту не удалось скачать или разобрать; текст можно показать пользователю."""


@dataclass
class FeedInfo:
    """Прочитанная лента."""
    url: str                 # итоговый адрес ленты (после редиректов и автопоиска)
    title: str
    site_link: str
    articles: list[Article] = field(default_factory=list)


@dataclass
class RefreshResult:
    """Итог опроса одной ленты: сколько записей прочитано, сколько новых сохранено."""
    feed_id: int
    url: str
    fetched: int = 0
    new: int = 0
    error: Optional[str] = None


# ------------------------------------------------------------------ текст
class _PlainText(HTMLParser):
    """Видимый текст из HTML анонса: теги убираются, сущности раскрываются."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._hidden = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in {"script", "style"}:
            self._hidden += 1
        elif tag in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"}:
            self._hidden = max(0, self._hidden - 1)
        elif tag in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._hidden:
            self._parts.append(data)

    def result(self) -> str:
        text = _SPACES.sub(" ", "".join(self._parts))
        return _NEWLINES.sub("\n", text).strip()


def html_to_text(value: Optional[str]) -> str:
    """HTML → обычный текст. В БД и интерфейс попадает уже чистый текст, без чужой разметки."""
    if not value:
        return ""
    parser = _PlainText()
    parser.feed(value)
    parser.close()
    return parser.result()


# ------------------------------------------------------------------ разбор
def _entry_date(entry) -> Optional[datetime]:
    # feedparser приводит даты к UTC; лента без часового пояса считается UTC
    for key in ("published_parsed", "updated_parsed", "created_parsed"):
        parsed = entry.get(key)
        if parsed:
            try:
                return datetime(*parsed[:6], tzinfo=timezone.utc)
            except (TypeError, ValueError):
                continue
    return None


def _entry_summary(entry) -> str:
    raw = entry.get("summary") or ""
    if not raw and entry.get("content"):
        raw = entry["content"][0].get("value", "")
    text = html_to_text(raw)
    if len(text) > MAX_SUMMARY_CHARS:
        text = text[:MAX_SUMMARY_CHARS].rsplit(" ", 1)[0] + "…"
    return text


def _entry_guid(entry, title: str, link: str, summary: str) -> str:
    guid = (entry.get("id") or "").strip() or link
    if guid:
        return guid
    # нет ни guid, ни ссылки: устойчивый хеш содержимого, чтобы повторный опрос не дал дубль
    digest = hashlib.sha1(f"{title}\n{summary}".encode("utf-8")).hexdigest()[:20]
    return f"sha1:{digest}"


def parse_feed(content: bytes | str, url: str = "", feed_id: int = 0,
               content_type: Optional[str] = None) -> FeedInfo:
    """Разобрать готовый текст ленты (RSS 0.9x/1.0/2.0, Atom). Сеть не нужна.

    Article.id = 0: настоящий id назначает БД. Дубли по guid внутри ленты отбрасываются,
    статья без даты получает время чтения.
    """
    headers = {"content-type": content_type} if content_type else None
    parsed = feedparser.parse(content, response_headers=headers)
    if not parsed.get("version") and not parsed.entries:
        reason = parsed.get("bozo_exception")
        raise FeedError(f"по адресу {url or '(текст)'} нет RSS/Atom-ленты"
                        + (f": {reason}" if reason else ""))

    meta = parsed.get("feed", {})
    now = datetime.now(timezone.utc)
    articles: list[Article] = []
    seen: set[str] = set()
    for entry in parsed.entries:
        link = (entry.get("link") or "").strip()
        if link and url:
            link = urljoin(url, link)  # относительная ссылка; пустую не трогаем, иначе она станет адресом ленты
        summary = _entry_summary(entry)
        title = html_to_text(entry.get("title")) or summary[:80] or "(без заголовка)"
        guid = _entry_guid(entry, title, link, summary)
        if guid in seen:
            continue
        seen.add(guid)
        articles.append(Article(
            id=0, feed_id=feed_id, guid=guid, title=title, summary=summary,
            link=link, published_at=_entry_date(entry) or now,
        ))

    return FeedInfo(
        url=url,
        title=html_to_text(meta.get("title")) or urlparse(url).netloc or url,
        site_link=meta.get("link") or "",
        articles=articles,
    )


# ------------------------------------------------------------------ сеть
class _AlternateLinks(HTMLParser):
    """<link rel="alternate" type="application/rss+xml" href="..."> на HTML-странице сайта."""

    def __init__(self) -> None:
        super().__init__()
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag != "link":
            return
        a = {k.lower(): (v or "") for k, v in attrs}
        if "alternate" in a.get("rel", "").lower().split() and a.get("type", "").lower() in _FEED_TYPES:
            if a.get("href"):
                self.links.append(a["href"])


def normalize_url(url: str) -> str:
    """Адрес, введённый человеком: «lenta.ru/rss» → «https://lenta.ru/rss». Только http/https."""
    url = (url or "").strip()
    if not url:
        raise FeedError("адрес ленты пустой")
    if "://" not in url:
        url = "https://" + url
    parts = urlparse(url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise FeedError(f"неподдерживаемый адрес ленты: {url}")
    return url


def _download(client: httpx.Client, url: str) -> tuple[bytes, str, str]:
    """(содержимое, content-type, итоговый адрес после редиректов)."""
    try:
        with client.stream("GET", url) as resp:
            if resp.status_code >= 400:
                raise FeedError(f"сервер ответил {resp.status_code} на {url}")
            chunks, size = [], 0
            for chunk in resp.iter_bytes():
                size += len(chunk)
                if size > MAX_FEED_BYTES:
                    raise FeedError(f"лента {url} больше {MAX_FEED_BYTES // (1024 * 1024)} МБ")
                chunks.append(chunk)
            return b"".join(chunks), resp.headers.get("content-type", ""), str(resp.url)
    except httpx.TimeoutException:
        raise FeedError(f"лента {url} не ответила за отведённое время") from None
    except httpx.HTTPError as exc:
        raise FeedError(f"не удалось скачать {url}: {exc}") from None


def make_client(timeout: float = DEFAULT_TIMEOUT) -> httpx.Client:
    return httpx.Client(timeout=timeout, follow_redirects=True,
                        headers={"User-Agent": USER_AGENT, "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*"})


def read_feed(url: str, feed_id: int = 0, *, client: Optional[httpx.Client] = None) -> FeedInfo:
    """Скачать и разобрать ленту. Если по адресу страница сайта, ищет на ней ссылку на RSS."""
    url = normalize_url(url)
    own = client is None
    client = client or make_client()
    try:
        content, ctype, final_url = _download(client, url)
        try:
            return parse_feed(content, final_url, feed_id, ctype)
        except FeedError:
            if "html" not in ctype.lower() and not content.lstrip()[:200].lower().startswith((b"<!doctype html", b"<html")):
                raise
            finder = _AlternateLinks()
            finder.feed(content.decode("utf-8", errors="replace"))
            if not finder.links:
                raise FeedError(f"{url} — страница сайта, ссылку на RSS на ней найти не удалось") from None
            feed_url = urljoin(final_url, finder.links[0])
            content, ctype, final_url = _download(client, feed_url)
            return parse_feed(content, final_url, feed_id, ctype)
    finally:
        if own:
            client.close()


def fetch_feed(url: str, feed_id: int = 0, *, client: Optional[httpx.Client] = None) -> list[Article]:
    """Новые статьи ленты без дублей (контракт из docs/PLAN.md). Ошибки — FeedError."""
    return read_feed(url, feed_id, client=client).articles


# ------------------------------------------------------------------ опрос
SaveFn = Callable[[int, list[Article]], int]


def refresh_feeds(feeds: Iterable[tuple[int, str]], save: SaveFn, *, max_workers: int = 8,
                  client: Optional[httpx.Client] = None) -> list[RefreshResult]:
    """Опросить ленты параллельно и сохранить статьи.

    feeds — пары (feed_id, url); save(feed_id, articles) сохраняет и возвращает число новых статей.
    Упавшая лента не мешает остальным: ошибка попадает в RefreshResult.error.
    """
    feeds = list(feeds)
    if not feeds:
        return []
    own = client is None
    client = client or make_client()

    def one(feed: tuple[int, str]) -> RefreshResult:
        feed_id, url = feed
        result = RefreshResult(feed_id=feed_id, url=url)
        try:
            articles = fetch_feed(url, feed_id, client=client)
            result.fetched = len(articles)
            result.new = save(feed_id, articles) or 0
        except FeedError as exc:
            result.error = str(exc)
        except Exception as exc:  # ошибка сохранения не должна останавливать опрос
            log.exception("лента %s: ошибка сохранения", url)
            result.error = f"ошибка сохранения: {exc}"
        return result

    try:
        with ThreadPoolExecutor(max_workers=min(max_workers, len(feeds))) as pool:
            results = list(pool.map(one, feeds))
    finally:
        if own:
            client.close()
    for r in results:
        if r.error:
            log.warning("лента %s: %s", r.url, r.error)
    return results


class Poller:
    """Фоновый опрос лент раз в `interval` секунд плюс обновление по кнопке.

        poller = Poller(repository.list_feed_urls, repository.save_articles, interval=300)
        poller.start()               # в lifespan FastAPI, внутри работающего event loop
        await poller.refresh_now()   # POST /api/refresh
        await poller.stop()          # при остановке сервиса

    get_feeds вызывается перед каждым опросом, поэтому новые каналы подхватываются без перезапуска.
    Одновременно идёт только один опрос: кнопка во время фонового опроса дождётся его и запустит свой.
    """

    def __init__(self, get_feeds: Callable[[], Sequence[tuple[int, str]]], save: SaveFn,
                 interval: float = 300.0, *, client: Optional[httpx.Client] = None) -> None:
        self.get_feeds = get_feeds
        self.save = save
        self.interval = interval
        self.client = client
        self.last_results: list[RefreshResult] = []
        self._lock = asyncio.Lock()
        self._task: Optional[asyncio.Task] = None

    async def refresh_now(self) -> list[RefreshResult]:
        async with self._lock:
            feeds = list(self.get_feeds())
            self.last_results = await asyncio.to_thread(
                refresh_feeds, feeds, self.save, client=self.client)
            return self.last_results

    async def _loop(self) -> None:
        while True:
            try:
                await self.refresh_now()
            except Exception:
                log.exception("ошибка фонового опроса лент")
            await asyncio.sleep(self.interval)

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.get_running_loop().create_task(self._loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None


__all__ = [
    "FeedError", "FeedInfo", "RefreshResult", "Poller",
    "fetch_feed", "read_feed", "parse_feed", "refresh_feeds", "normalize_url", "html_to_text",
]
