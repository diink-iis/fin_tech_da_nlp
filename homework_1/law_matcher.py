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

_QUOTES = str.maketrans({"«": '"', "»": '"', "“": '"', "”": '"', "„": '"', "’": "'", "‐": "-", "‑": "-", "–": "-", "—": "-", "ё": "е"})
_WORDS = re.compile(r"[a-zа-я0-9]+(?:[-.][a-zа-я0-9]+)*", re.IGNORECASE)
_QTITLE = re.compile(r'["«]([^"»«]{2,})["»]')
_LEGAL_PREFIX = re.compile(r"^(?:(?:федеральн(?:ый|ого|ому|ым|ом)|фз|ф\s*з)\s+)?(?:закон(?:а|у|ом|е)?|федеральн(?:ый|ого|ому|ым|ом)\s+закон(?:а|у|ом|е)?)\s+", re.IGNORECASE)


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


def _canonical(text: str) -> str:
    tokens = _tokens(text)
    if not tokens:
        return ""
    if _MORPH is not None:
        # Preserve numeric act identifiers (e.g. 152-ФЗ) and abbreviations.
        return " ".join(_lemma(t) if t.isalpha() and len(t) > 3 else t for t in tokens)
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


def initialize(path: str | Path | None = None) -> None:
    """Prepare index once at FastAPI lifespan startup. Optional custom JSON path."""
    global _INDEX, _EXACT_INDEX, _MORPH, _STEMMER
    with _LOCK:
        if _INDEX is not None and path is None:
            return
        if pymorphy3 is not None and _MORPH is None:
            _MORPH = pymorphy3.MorphAnalyzer()
        elif pymorphy3 is None and RussianStemmer is not None and _STEMMER is None:
            _STEMMER = RussianStemmer()
        index, exact = _make_indexes(Path(path) if path is not None else _DATA_FILE)
        _INDEX, _EXACT_INDEX = index, exact


def find_law(law_text: Optional[str]) -> Optional[int]:
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
