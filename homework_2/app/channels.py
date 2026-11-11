"""Свой тестовый RSS-канал: создание канала, публикация статей, отдача RSS 2.0.

Канал хранится в SQLite (таблицы `channels` и `channel_posts`, создаются сами) и отдаётся
тем же приложением по адресу /rss/{id}.xml. Агрегатор читает его так же, как любую внешнюю ленту.

    store = ChannelStore("news.db")
    channel = store.create_channel("Тестовый канал", "Посты для демонстрации")
    store.post_article(channel.id, "ЦБ снизил ключевую ставку", "Текст статьи…")
    xml = store.render_rss(channel.id, base_url="http://localhost:8000")   # GET /rss/{id}.xml

Модуль не импортирует остальные модули приложения, кроме общих типов.
"""
from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse
from xml.etree import ElementTree as ET

MAX_TITLE = 300
MAX_TEXT = 20_000
ATOM_NS = "http://www.w3.org/2005/Atom"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS channels (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    title       TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS channel_posts (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_id   INTEGER NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
    title        TEXT NOT NULL,
    text         TEXT NOT NULL DEFAULT '',
    link         TEXT,
    published_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS channel_posts_by_channel ON channel_posts(channel_id, id);
"""


class ChannelError(ValueError):
    """Неверные данные поста или неизвестный канал; текст можно показать пользователю."""


@dataclass
class Channel:
    id: int
    title: str
    description: str
    created_at: datetime


@dataclass
class ChannelPost:
    id: int
    channel_id: int
    title: str
    text: str
    link: Optional[str]
    published_at: datetime


def channel_path(channel_id: int) -> str:
    """Путь, по которому приложение отдаёт канал."""
    return f"/rss/{channel_id}.xml"


def channel_feed_url(base_url: str, channel_id: int) -> str:
    """Полный адрес канала: его роль 4 сохраняет как обычную ленту и подписывает пользователя."""
    return base_url.rstrip("/") + channel_path(channel_id)


def post_guid(post: ChannelPost) -> str:
    return f"channel-{post.channel_id}-post-{post.id}"


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _clean(value: Optional[str], name: str, limit: int, required: bool) -> str:
    value = (value or "").strip()
    if required and not value:
        raise ChannelError(f"{name}: поле не заполнено")
    if len(value) > limit:
        raise ChannelError(f"{name}: не длиннее {limit} символов")
    return value


def _clean_link(link: Optional[str]) -> Optional[str]:
    link = (link or "").strip()
    if not link:
        return None
    parts = urlparse(link)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise ChannelError("ссылка должна начинаться с http:// или https://")
    return link


# ------------------------------------------------------------------ RSS
def render_rss(channel: Channel, posts: list[ChannelPost], base_url: str) -> str:
    """RSS 2.0 для канала; посты от новых к старым. Чистая функция без БД."""
    base = base_url.rstrip("/")
    self_url = channel_feed_url(base, channel.id)
    ET.register_namespace("atom", ATOM_NS)

    rss = ET.Element("rss", {"version": "2.0"})
    ch = ET.SubElement(rss, "channel")
    ET.SubElement(ch, "title").text = channel.title
    ET.SubElement(ch, "link").text = base + "/"
    ET.SubElement(ch, "description").text = channel.description or channel.title
    ET.SubElement(ch, "language").text = "ru"
    ET.SubElement(ch, f"{{{ATOM_NS}}}link", {"href": self_url, "rel": "self", "type": "application/rss+xml"})
    newest = max((p.published_at for p in posts), default=channel.created_at)
    ET.SubElement(ch, "lastBuildDate").text = format_datetime(newest, usegmt=True)

    for post in sorted(posts, key=lambda p: (p.published_at, p.id), reverse=True):
        item = ET.SubElement(ch, "item")
        ET.SubElement(item, "title").text = post.title
        ET.SubElement(item, "link").text = post.link or f"{self_url}#post-{post.id}"
        ET.SubElement(item, "description").text = post.text
        ET.SubElement(item, "guid", {"isPermaLink": "false"}).text = post_guid(post)
        ET.SubElement(item, "pubDate").text = format_datetime(post.published_at, usegmt=True)

    ET.indent(rss)
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(rss, encoding="unicode") + "\n"


# ------------------------------------------------------------------ хранилище
class ChannelStore:
    """Каналы и посты в SQLite. Новое соединение на каждый вызов — безопасно из разных потоков."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = str(db_path)
        with closing(self._connect()) as conn, conn:
            conn.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    @staticmethod
    def _channel(row: sqlite3.Row) -> Channel:
        return Channel(row["id"], row["title"], row["description"],
                       datetime.fromisoformat(row["created_at"]))

    @staticmethod
    def _post(row: sqlite3.Row) -> ChannelPost:
        return ChannelPost(row["id"], row["channel_id"], row["title"], row["text"], row["link"],
                           datetime.fromisoformat(row["published_at"]))

    def create_channel(self, title: str, description: str = "") -> Channel:
        title = _clean(title, "название канала", MAX_TITLE, required=True)
        description = _clean(description, "описание канала", MAX_TEXT, required=False)
        created = _now()
        with closing(self._connect()) as conn, conn:
            cur = conn.execute(
                "INSERT INTO channels (title, description, created_at) VALUES (?, ?, ?)",
                (title, description, created.isoformat()))
            return Channel(cur.lastrowid, title, description, created)

    def get_channel(self, channel_id: int) -> Optional[Channel]:
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT * FROM channels WHERE id = ?", (channel_id,)).fetchone()
        return self._channel(row) if row else None

    def list_channels(self) -> list[Channel]:
        with closing(self._connect()) as conn:
            rows = conn.execute("SELECT * FROM channels ORDER BY id").fetchall()
        return [self._channel(r) for r in rows]

    def post_article(self, channel_id: int, title: str, text: str = "",
                     link: Optional[str] = None) -> ChannelPost:
        title = _clean(title, "заголовок", MAX_TITLE, required=True)
        text = _clean(text, "текст", MAX_TEXT, required=False)
        link = _clean_link(link)
        published = _now()
        with closing(self._connect()) as conn, conn:
            if conn.execute("SELECT 1 FROM channels WHERE id = ?", (channel_id,)).fetchone() is None:
                raise ChannelError(f"канала {channel_id} нет")
            cur = conn.execute(
                "INSERT INTO channel_posts (channel_id, title, text, link, published_at) VALUES (?, ?, ?, ?, ?)",
                (channel_id, title, text, link, published.isoformat()))
            return ChannelPost(cur.lastrowid, channel_id, title, text, link, published)

    def list_posts(self, channel_id: int, limit: int = 100) -> list[ChannelPost]:
        """Последние посты канала, от новых к старым."""
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT * FROM channel_posts WHERE channel_id = ? ORDER BY id DESC LIMIT ?",
                (channel_id, limit)).fetchall()
        return [self._post(r) for r in rows]

    def delete_post(self, channel_id: int, post_id: int) -> bool:
        with closing(self._connect()) as conn, conn:
            cur = conn.execute("DELETE FROM channel_posts WHERE channel_id = ? AND id = ?",
                               (channel_id, post_id))
            return cur.rowcount > 0

    def render_rss(self, channel_id: int, base_url: str, limit: int = 100) -> str:
        """Текст ленты для GET /rss/{id}.xml. Неизвестный канал — ChannelError (в API это 404)."""
        channel = self.get_channel(channel_id)
        if channel is None:
            raise ChannelError(f"канала {channel_id} нет")
        return render_rss(channel, self.list_posts(channel_id, limit), base_url)


__all__ = [
    "Channel", "ChannelPost", "ChannelError", "ChannelStore",
    "render_rss", "channel_feed_url", "channel_path", "post_guid",
]
