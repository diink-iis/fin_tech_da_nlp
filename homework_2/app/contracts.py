"""Общие типы на стыке модулей (черновик по docs/PLAN.md, раздел 3).

Файл принадлежит роли 4 (бэкенд и интеграция); изменения вносятся только по согласию всей команды.
Здесь он нужен, чтобы семантический движок можно было разрабатывать и тестировать отдельно.
"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Optional, Protocol


@dataclass
class Article:
    """Новость в хранилище."""
    id: int
    feed_id: int
    guid: str
    title: str
    summary: str
    link: str
    published_at: Optional[datetime] = None


@dataclass
class Filter:
    """Фильтр пользователя.

    kind="keyword": params = {"any": [...], "all": [...], "none": [...], "fields": ["title", "summary"]}
    kind="topic":   params = {"description": "...", "examples": ["..."], "threshold": 0.45}
    """
    id: int
    user_id: int
    name: str
    kind: Literal["keyword", "topic"]
    params: dict = field(default_factory=dict)


@dataclass
class MatchReason:
    """Почему новость попала в выдачу; показывается в интерфейсе.

    keyword: details = {"matched": ["нефть"], "in": "title"}
    topic:   details = {"score": 0.62, "threshold": 0.45, "best_phrase": "..."}
    """
    filter_id: int
    filter_name: str
    kind: str
    details: dict = field(default_factory=dict)


class FilterEngine(Protocol):
    def match(self, article: Article, flt: Filter) -> Optional[MatchReason]: ...
