"""Разбирает номера из RawLink и возвращает список LawLink.
Поиск ссылок и определение закона — задачи других модулей.
"""
from __future__ import annotations

import re
import sys
from itertools import product
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from contracts import RawLink
    from main import LawLink

# Чем могут разделяться номера в перечислении.
_MARK = "\x00"
_QUOTED_LETTER = re.compile("[\"«“„]([A-Za-zА-Яа-яЁё])[\"»”]")
_SEPARATOR = re.compile(r"\s*(?:[,;]|(?<!\x00)\b(?:и|или|либо)\b)\s*", re.IGNORECASE)
_NUMBER = re.compile(r"\d+(?:\.\d+)*(?:-\d+(?:\.\d+)*)?\Z")
_LETTER = re.compile(r"[A-Za-zА-Яа-яЁё](?:-[A-Za-zА-Яа-яЁё])?\Z")
_ROMAN = re.compile(r"[IVXLCDMivxlcdm]+\Z")
_DASHES = str.maketrans("‐‑‒–—−", "------")
_WRAPPERS = " \t\r\n\u00a0\u202f\"'«»„“”()[]{}."


def _clean_item(item: str) -> str:
    item = item.strip(_WRAPPERS)
    if item.startswith("№"):
        item = item[1:].strip(_WRAPPERS)
    item = item.translate(_DASHES)
    # Номера вроде 4.6 и 43.2-6 не разбиваем.
    item = re.sub(r"(?<=\w)\s*-\s*(?=\w)", "-", item)
    item = re.sub(r"(?<=\d)\s*\.\s*(?=\d)", ".", item)
    return item


_ORDINALS = {"перв": "1", "втор": "2", "трет": "3", "четвёрт": "4", "четверт": "4", "пят": "5",
             "шест": "6", "седьм": "7", "восьм": "8", "девят": "9", "десят": "10"}
_ORDINAL = re.compile(r"(перв|втор|трет|четвёрт|четверт|пят|шест|седьм|восьм|девят|десят)"
                      r"(?:ая|ой|ую|ые|ых|ого|ое|ому)\Z", re.IGNORECASE)


def _parse_numbers(value: str | None) -> list[str | None]:
    """Разбирает номера, убирает повторы и сохраняет порядок."""
    if value is None:
        return [None]
    if not isinstance(value, str) or not value.strip():
        return []
    result: list[str] = []
    seen: set[str] = set()
    # буква в кавычках («и», "к") — это значение, а не союз
    value = _QUOTED_LETTER.sub(lambda m: _MARK + m.group(1), value)
    for part in _SEPARATOR.split(value):
        number = _clean_item(part.replace(_MARK, ""))
        word = _ORDINAL.match(number)
        if word:  # "части первой" -> "1"
            number = _ORDINALS[word.group(1).lower()]
        if not number:
            continue
        if not (_NUMBER.fullmatch(number) or _LETTER.fullmatch(number)
                or _ROMAN.fullmatch(number)):
            return []
        if number not in seen:
            seen.add(number)
            result.append(number)
    return result


def _law_link_class() -> type[LawLink]:
    """Импортируем LawLink при вызове, чтобы не было циклического импорта."""
    entry = sys.modules.get("__main__")
    entry_file = str(getattr(entry, "__file__", "")).replace("\\", "/")
    if entry_file == "main.py" or entry_file.endswith("/main.py"):
        model = getattr(entry, "LawLink", None)
        if model is not None:
            return model
    from main import LawLink
    return LawLink


def expand(raw: RawLink, law_id: int | None) -> list[LawLink]:
    """Создаёт LawLink для каждой комбинации номеров.

    Пропущенные уровни и law_id остаются None.
    """
    if raw is None or (law_id is not None and
                       (not isinstance(law_id, int) or isinstance(law_id, bool))):
        return []
    articles = _parse_numbers(getattr(raw, "article", None))
    points = _parse_numbers(getattr(raw, "point", None))
    subpoints = _parse_numbers(getattr(raw, "subpoint", None))
    if not articles or not points or not subpoints:
        return []
    if articles == [None] and points == [None] and subpoints == [None]:
        return []

    LawLinkModel = _law_link_class()
    return [
        LawLinkModel(law_id=law_id, article=article,
                     point_article=point, subpoint_article=subpoint)
        for article, point, subpoint in product(articles, points, subpoints)
    ]
