"""Подготовить локальную ленту с учебными публикациями для первого показа."""
from __future__ import annotations

import os
from pathlib import Path
from tempfile import TemporaryDirectory

from app.aggregator import parse_feed
from app.channels import ChannelStore, channel_feed_url
from app.db import Repository


def prepare_demo(path: Path, base_url: str) -> None:
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    # Publish a fully populated database once, including on simultaneous launches.
    with TemporaryDirectory(dir=path.parent) as tmp:
        database = Path(tmp) / "demo.db"
        repo = Repository(database)
        channels = ChannelStore(database)
        anna = repo.create_user("Аня")["id"]
        boris = repo.create_user("Борис")["id"]
        repo.create_filter(anna, "Нефть", "keyword", {"any": ["нефть"]})
        repo.create_filter(anna, "Политика", "topic", {
            "description": "Политика и международные отношения: правительства, президенты, "
                           "министры, выборы, санкции, переговоры, дипломатия",
            "threshold": 0.45,
        })
        repo.create_filter(boris, "Банки", "keyword", {"any": ["банк"]})
        channel = channels.create_channel("Учебная редакция", "Примеры новостей для демонстрации фильтров.")
        url = channel_feed_url(base_url, channel.id)
        feed = repo.ensure_channel_feed(channel.id, channel.title, url, owner_user_id=anna)
        repo.subscribe(anna, feed["id"])
        repo.subscribe(boris, feed["id"])
        channels.post_article(channel.id, "Нефть подорожала", "Котировки нефти выросли по итогам торгов.")
        channels.post_article(channel.id, "Банк открыл отделение", "В новом офисе клиенты могут оформить вклад.")
        channels.post_article(channel.id, "Президент провёл переговоры", "Министры обсудили санкции.")
        rss = channels.render_rss(channel.id, base_url)
        repo.save_articles(feed["id"], parse_feed(rss, url, feed["id"]).articles)
        try:
            os.link(database, path)
        except FileExistsError:
            pass  # Another launcher finished first.


if __name__ == "__main__":
    default_path = Path(__file__).resolve().parents[1] / "data" / "demo.db"
    path = Path(os.environ.get("RSS_DB_PATH", default_path))
    base_url = os.environ.get("RSS_DEMO_BASE_URL", "http://127.0.0.1:8000")
    prepare_demo(path, base_url)
    print(f"Демонстрационная база готова ({path}).")
