"""Match an already extracted legal-act name to its ID in law_aliases.json.

Public API:
    initialize(path=None) -> None  # optional eager initialization at application startup
    find_law(law_text: str | None) -> int | None

This module does not extract names from the original text and has no dependency
on link_extractor, number_parser or main.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from threading import Lock
from typing import Optional

try:
    import pymorphy3  # recommended for full Russian lemmatization
except ImportError:
    pymorphy3 = None

try:
    from nltk.stem.snowball import RussianStemmer
except ImportError:
    RussianStemmer = None

_DATA_FILE = Path(__file__).resolve().with_name("law_aliases.json")
_LOCK = Lock()
_INDEX: dict[str, set[int]] | None = None
_EXACT_INDEX: dict[str, set[int]] | None = None
_MORPH = None
_STEMMER = None
_NUM_INDEX: dict[str, dict[str, set[int]]] | None = None  # "152-ФЗ" -> {дата|"": {law_id}}
_NUM_TITLES: dict[str, list[tuple[int, str]]] | None = None  # "152-ФЗ" -> [(law_id, название)]

_QUOTES = str.maketrans({"«": '"', "»": '"', "“": '"', "”": '"', "„": '"', "’": "'", "‐": "-", "‑": "-", "–": "-", "—": "-", "ё": "е"})
_WORDS = re.compile(r"[a-zа-я0-9]+(?:[-.][a-zа-я0-9]+)*", re.IGNORECASE)
_QTITLE = re.compile(r'["«]([^"»«]{2,})["»]')
_LEGAL_PREFIX = re.compile(r"^(?:(?:федеральн(?:ый|ого|ому|ым|ом)|фз|ф\s*з)\s+)?(?:закон(?:а|у|ом|е)?|федеральн(?:ый|ого|ому|ым|ом)\s+закон(?:а|у|ом|е)?)\s+", re.IGNORECASE)


_ACT_NUMBER = re.compile(r"(?<![\d.])(\d{1,4})\s*-?\s*(фкз|фз|кз|з|[ivx]+)(?![а-яa-z0-9])", re.IGNORECASE)
_DATE_NUM = re.compile(r"(\d{2})\.(\d{2})\.(\d{4})")
_DATE_WORDS = re.compile(r"(\d{1,2})\s+([а-я]+)\s+(\d{4})", re.IGNORECASE)
_MONTHS = {m: i for i, m in enumerate(
    "января февраля марта апреля мая июня июля августа сентября октября ноября декабря".split(), 1)}


def _act_numbers(text: str) -> list[str]:
    """Номера актов вида 152-ФЗ, 'N 152 ФЗ', '№ 2300-I' -> ['152-ФЗ', ...]."""
    cleaned = _DATE_NUM.sub(" ", text.replace("№", " "))
    return [f"{n}-{suffix.upper()}" for n, suffix in _ACT_NUMBER.findall(cleaned)]


def _act_date(text: str) -> str:
    m = _DATE_NUM.search(text)
    if m:
        return f"{m.group(1)}.{m.group(2)}.{m.group(3)}"
    for day, month, year in _DATE_WORDS.findall(text.casefold()):
        if month in _MONTHS:
            return f"{int(day):02d}.{_MONTHS[month]:02d}.{year}"
    return ""


def _normal(text: str) -> str:
    """Conservative normalization: formatting + inflectional normalization."""
    text = text.casefold().translate(_QUOTES)
    text = re.sub(r"\bномер\b", "№", text)
    text = re.sub(r"\s*№\s*", " №", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _tokens(text: str) -> tuple[str, ...]:
    return tuple(_WORDS.findall(_normal(text)))


@lru_cache(maxsize=None)
def _lemma(token: str) -> str:
    """Cached: the same words repeat across thousands of aliases."""
    return _MORPH.parse(token)[0].normal_form


def _lemma_token(token: str) -> str:
    """Лемма слова; для дефисных слов ('уголовно-исполнительного') — по частям."""
    parts = token.split("-")
    if all(p.isalpha() for p in parts):
        return "-".join(_lemma(p) if len(p) > 3 else p for p in parts)
    return token


def _canonical(text: str) -> str:
    tokens = _tokens(text)
    if not tokens:
        return ""
    if _MORPH is not None:
        # Preserve numeric act identifiers (e.g. 152-ФЗ) and abbreviations.
        return " ".join(_lemma_token(t) for t in tokens)
    if _STEMMER is not None:
        return " ".join(_STEMMER.stem(t) if t.isalpha() and len(t) > 3 else t for t in tokens)
    # Minimal, dependency-free support for frequent legal forms.
    replacements = {
        "кодекса": "кодекс", "кодексу": "кодекс", "кодексом": "кодекс", "кодексе": "кодекс",
        "закона": "закон", "закону": "закон", "законом": "закон", "законе": "закон",
        "конституции": "конституция", "конституцию": "конституция", "конституцией": "конституция",
        "российской": "российская", "российскую": "российская", "федерации": "федерация",
        "федерального": "федеральный", "федеральному": "федеральный",
        "уголовного": "уголовный", "налогового": "налоговый", "гражданского": "гражданский",
        "семейного": "семейный", "трудового": "трудовой",
        "процессуального": "процессуальный", "арбитражного": "арбитражный",
    }
    return " ".join(replacements.get(t, t) for t in tokens)


def _variants(alias: str) -> set[str]:
    """Additional safe variants: act title may be cited without its type."""
    variants = {alias}
    match = _QTITLE.search(alias)
    if match and match.group(1).strip():
        # Quoted official title is unambiguous only if it identifies the act.
        variants.add(match.group(1).strip())
        variants.add('Закон "' + match.group(1).strip() + '"')
        variants.add('Федеральный закон "' + match.group(1).strip() + '"')
    return variants


def _make_indexes(path: Path) -> tuple[dict[str, set[int]], dict[str, set[int]]]:
    with path.open("r", encoding="utf-8-sig") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("law_aliases.json must be a mapping")
    index: dict[str, set[int]] = {}
    exact: dict[str, set[int]] = {}
    for law_id_str, aliases in data.items():
        law_id = int(law_id_str)
        if not isinstance(aliases, list):
            continue
        for alias in aliases:
            if not isinstance(alias, str) or not alias.strip():
                continue
            for variant in _variants(alias):
                key = _canonical(variant)
                raw = ' '.join(_tokens(variant))
                if key:
                    index.setdefault(key, set()).add(law_id)
                if raw:
                    exact.setdefault(raw, set()).add(law_id)
    return index, exact


def _make_number_index(path: Path) -> tuple[dict, dict]:
    """Индекс актов по номеру (и дате): 'Закона № 580-ФЗ' без названия."""
    with path.open("r", encoding="utf-8-sig") as f:
        data = json.load(f)
    by_number: dict[str, dict[str, set[int]]] = {}
    titles: dict[str, list[tuple[int, str]]] = {}
    for law_id_str, aliases in data.items():
        law_id = int(law_id_str)
        for alias in aliases:
            if not isinstance(alias, str) or "№" not in alias:
                continue
            numbers = _act_numbers(alias)
            if len(numbers) != 1:
                continue
            number, date = numbers[0], _act_date(alias)
            by_number.setdefault(number, {}).setdefault(date, set()).add(law_id)
            title = _QTITLE.search(alias)
            if title:
                titles.setdefault(number, []).append((law_id, _canonical(title.group(1))))
    return by_number, titles


def _find_by_number(law_text: str) -> Optional[int]:
    """Закон по номеру ('№ 580-ФЗ'); при нескольких кандидатах — по дате или названию."""
    numbers = _act_numbers(law_text)
    if len(numbers) != 1 or _NUM_INDEX is None:
        return None
    by_date = _NUM_INDEX.get(numbers[0])
    if not by_date:
        return None
    candidates = set().union(*by_date.values())
    if len(candidates) == 1:
        return next(iter(candidates))
    date = _act_date(law_text)
    if date and len(by_date.get(date, ())) == 1:
        return next(iter(by_date[date]))
    title = _QTITLE.search(_normal(law_text))
    if title and _NUM_TITLES:
        wanted = _canonical(title.group(1))
        ids = {i for i, t in _NUM_TITLES.get(numbers[0], []) if t == wanted}
        if len(ids) == 1:
            return next(iter(ids))
    return None


def initialize(path: str | Path | None = None) -> None:
    """Prepare index once at FastAPI lifespan startup. Optional custom JSON path."""
    global _INDEX, _EXACT_INDEX, _MORPH, _STEMMER, _NUM_INDEX, _NUM_TITLES
    with _LOCK:
        if _INDEX is not None and path is None:
            return
        if pymorphy3 is not None and _MORPH is None:
            _MORPH = pymorphy3.MorphAnalyzer()
        elif pymorphy3 is None and RussianStemmer is not None and _STEMMER is None:
            _STEMMER = RussianStemmer()
        source = Path(path) if path is not None else _DATA_FILE
        index, exact = _make_indexes(source)
        _INDEX, _EXACT_INDEX = index, exact
        _NUM_INDEX, _NUM_TITLES = _make_number_index(source)


def find_law(law_text: Optional[str]) -> Optional[int]:
    """Return known law ID or None, never guess by approximate similarity.

    Name lookup first; if the name is ambiguous or unknown, the act number
    ("№ 580-ФЗ"), date and quoted title are used.
    """
    law_id = _find_by_name(law_text)
    if law_id is None and isinstance(law_text, str):
        try:
            return _find_by_number(law_text)
        except (ValueError, TypeError):
            return None
    return law_id


def _find_by_name(law_text: Optional[str]) -> Optional[int]:
    """Return known law ID or None, never guess by approximate similarity.

    A law_text value is a name extracted by link_extractor, not the full passage.
    Ambiguous normalized aliases are deliberately rejected.
    """
    try:
        if not isinstance(law_text, str) or not law_text.strip():
            return None
        if _INDEX is None:
            initialize()
        assert _INDEX is not None and _EXACT_INDEX is not None

        raw_tokens = _tokens(law_text)
        raw_key = " ".join(raw_tokens)
        raw_hits = _EXACT_INDEX.get(raw_key, set())
        if len(raw_hits) == 1:
            return next(iter(raw_hits))
        if len(raw_hits) > 1:
            return None

        key = _canonical(law_text)
        hits = _INDEX.get(key, set())
        if len(hits) == 1:
            return next(iter(hits))
        if len(hits) > 1:
            return None

        # A clipped law name can contain a more precise alias. Only accept
        # complete token sequences, not substring matches inside other words.
        # Prefer the longest unambiguous match; reject equal-length conflicts.
        parts = key.split()
        matches: list[tuple[int, int]] = []
        for start in range(len(parts)):
            for end in range(start + 1, len(parts) + 1):
                length = end - start
                if length < 2 and len(parts) > 1:
                    continue
                ids = _INDEX.get(" ".join(parts[start:end]), set())
                if len(ids) == 1:
                    matches.append((length, next(iter(ids))))
        if not matches:
            return None
        maximum = max(length for length, _ in matches)
        winners = {id_ for length, id_ in matches if length == maximum}
        return next(iter(winners)) if len(winners) == 1 else None
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None


__all__ = ["find_law", "initialize"]
