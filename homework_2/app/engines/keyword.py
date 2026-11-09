
"""Keyword filtering for RSS articles.

The engine is independent of storage, HTTP and the UI. It implements the shared
Article/Filter -> MatchReason | None contract from app.contracts.

Filter params::

    {"any": ["банк", "нефть"],       # OR: at least one must match
     "all": ["Россия"],             # AND: every term must match
     "none": ["реклама"],           # NOT: none may match
     "fields": ["title", "summary"]} # optional; both by default

Terms may be single words or phrases. A phrase matches *consecutive lemmatized
words within the same field*, not arbitrary words scattered through an article.
Russian words are lemmatized using pymorphy3; other scripts are case-folded.

MatchReason.details keeps the plan's `matched` and `in` fields and adds JSON-
serializable explanations and character offsets for optional highlighting.
Offsets refer to `clean_text(article.title/summary)`, not raw RSS HTML.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from html.parser import HTMLParser
from typing import Callable, Iterable, Optional, Sequence

from app.contracts import Article, Filter, MatchReason

# Unicode letters and digits, without underscores. Hyphens and punctuation
# separate words, so "машинное-обучение" and "машинное обучение" both work.
_WORD = re.compile(r"[^\W_]+", re.UNICODE)
_SPACES = re.compile(r"\s+")
_SPLIT_INPUT = re.compile(r"[,;\n]+")
_RUSSIAN = re.compile(r"[а-я]")
_FIELDS = ("title", "summary")
_BLOCK_TAGS = frozenset({
    "address", "article", "blockquote", "br", "div", "h1", "h2", "h3",
    "h4", "h5", "h6", "hr", "li", "ol", "p", "section", "table", "td",
    "th", "tr", "ul",
})


class KeywordFilterError(ValueError):
    """Malformed filter or unavailable Russian morphological analyzer."""


class _PlainText(HTMLParser):
    """Extract visible text from RSS HTML (including HTML entities)."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._hidden: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        if tag in {"script", "style"}:
            self._hidden.append(tag)
        elif tag in _BLOCK_TAGS and not self._hidden:
            self._parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"} and tag in self._hidden:
            # Usually a single script/style; tolerate malformed nested tags.
            self._hidden.remove(tag)
        elif tag in _BLOCK_TAGS and not self._hidden:
            self._parts.append(" ")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        if tag in _BLOCK_TAGS and not self._hidden:
            self._parts.append(" ")

    def handle_data(self, data: str) -> None:
        if not self._hidden:
            self._parts.append(data)

    def result(self) -> str:
        return _SPACES.sub(" ", "".join(self._parts)).strip()


def clean_text(value: Optional[str]) -> str:
    """Turn an article field into plain text suitable for searching and UI.

    Never highlight raw RSS HTML: its tags/entities change string offsets and
    rendering unsanitized HTML in the frontend would be unsafe.
    """
    if not value:
        return ""
    if not isinstance(value, str):
        raise KeywordFilterError("текст новости должен быть строкой")
    parser = _PlainText()
    parser.feed(value)
    parser.close()
    return parser.result()


def _normalize(word: str) -> str:
    # ё and е are treated as equivalent (common in Russian news headlines).
    return unicodedata.normalize("NFKC", word).casefold().replace("ё", "е")


@dataclass(frozen=True)
class _Word:
    lemma: str
    start: int
    end: int


@dataclass(frozen=True)
class _Term:
    label: str
    lemmas: tuple[str, ...]


class KeywordEngine:
    """Deterministic keyword engine; can be reused for all users and feeds.

    `lemmatizer` injection allows tests without downloading dictionaries and
    gives the caller a way to supply an alternative Russian morphological
    analyzer. In normal operation, pymorphy3 is loaded once per engine.
    """

    def __init__(self, *, lemmatizer: Optional[Callable[[str], str]] = None) -> None:
        if lemmatizer is None:
            try:
                import pymorphy3
            except ImportError as exc:
                raise KeywordFilterError(
                    "для поиска по русским словоформам установите pymorphy3 "
                    "(pip install pymorphy3)"
                ) from exc
            morph = pymorphy3.MorphAnalyzer()
            lemmatizer = lambda word: morph.parse(word)[0].normal_form
        self._lemmatizer = lemmatizer
        # Instance-local LRU: a global @lru_cache on a method would retain
        # obsolete KeywordEngine instances if the backend ever recreates them.
        self._lemma = lru_cache(maxsize=50_000)(self._compute_lemma)

    def _compute_lemma(self, word: str) -> str:
        normalized = _normalize(word)
        if _RUSSIAN.search(normalized):
            return _normalize(self._lemmatizer(normalized))
        return normalized  # no Russian analyzer applied to English/digits

    def _tokens(self, text: str) -> list[_Word]:
        return [_Word(self._lemma(m.group()), m.start(), m.end())
                for m in _WORD.finditer(text)]

    def _terms(self, params: dict, key: str) -> list[_Term]:
        values = params.get(key, [])
        if values is None:
            values = []
        if isinstance(values, str):
            # Allows a UI to send "банк, кредит" as well as a list of strings.
            values = _SPLIT_INPUT.split(values)
        if not isinstance(values, (tuple, list)):
            raise KeywordFilterError(f"{key}: ожидается список строк или строка")

        result: list[_Term] = []
        seen: set[tuple[str, ...]] = set()
        for value in values:
            if not isinstance(value, str):
                raise KeywordFilterError(f"{key}: ключевое слово должно быть строкой")
            label = _SPACES.sub(" ", value).strip()
            lemmas = tuple(self._lemma(m.group()) for m in _WORD.finditer(label))
            if lemmas and lemmas not in seen:
                result.append(_Term(label=label, lemmas=lemmas))
                seen.add(lemmas)
        return result

    @staticmethod
    def _fields(params: dict) -> tuple[str, ...]:
        raw = params.get("fields", list(_FIELDS))
        if isinstance(raw, str):
            raw = _SPLIT_INPUT.split(raw)
        if not isinstance(raw, (tuple, list)):
            raise KeywordFilterError("fields: ожидается список 'title' и/или 'summary'")
        if not raw or any(not isinstance(field, str) or field not in _FIELDS for field in raw):
            raise KeywordFilterError("fields: допустимы только 'title' и 'summary'")
        # Stable order and no duplicates even if the UI sends ["summary", "title"].
        return tuple(field for field in _FIELDS if field in raw)

    @staticmethod
    def _occurrences(words: Sequence[_Word], lemmas: tuple[str, ...]) -> Iterable[tuple[int, int]]:
        """Yield start/end offsets for each *contiguous* phrase occurrence."""
        width = len(lemmas)
        for pos in range(len(words) - width + 1):
            if all(words[pos + j].lemma == lemma for j, lemma in enumerate(lemmas)):
                yield words[pos].start, words[pos + width - 1].end

    def match(self, article: Article, flt: Filter) -> Optional[MatchReason]:
        """Return an explanation if the filter passes, otherwise None.

        Formula: (OR any, if given) AND (AND all, if given) AND NOT any none.
        A completely empty filter matches nothing. A none-only filter is valid:
        it expresses "everything except articles containing these terms".
        """
        if flt.kind != "keyword":
            return None
        params = flt.params or {}
        if not isinstance(params, dict):
            raise KeywordFilterError("params фильтра должен быть объектом")

        any_terms = self._terms(params, "any")
        all_terms = self._terms(params, "all")
        none_terms = self._terms(params, "none")
        if not (any_terms or all_terms or none_terms):
            return None
        fields = self._fields(params)
        texts = {field: clean_text(getattr(article, field)) for field in fields}
        tokenized = {field: self._tokens(texts[field]) for field in fields}

        # Cache the location of each distinct phrase in this article. This also
        # avoids repeated morphology when one term belongs to multiple groups.
        locations: dict[tuple[str, ...], list[tuple[str, int, int]]] = {}

        def find(term: _Term) -> list[tuple[str, int, int]]:
            if term.lemmas not in locations:
                locations[term.lemmas] = [
                    (field, start, end)
                    for field in fields
                    for start, end in self._occurrences(tokenized[field], term.lemmas)
                ]
            return locations[term.lemmas]

        if any_terms and not any(find(term) for term in any_terms):
            return None
        if any(not find(term) for term in all_terms):
            return None
        if any(find(term) for term in none_terms):
            return None

        hits: list[dict] = []
        matched: list[str] = []
        for rule, terms in (("any", any_terms), ("all", all_terms)):
            for term in terms:
                occurrences = find(term)
                if not occurrences:
                    continue
                if term.label not in matched:
                    matched.append(term.label)
                for field, start, end in occurrences:
                    hits.append({
                        "term": term.label, "rule": rule, "field": field,
                        "start": start, "end": end, "text": texts[field][start:end],
                    })

        # Highlight each distinct span once, even when one term is both an any
        # and an all condition. Offsets are Unicode character (code-point) indexes.
        highlights = {
            field: [{"start": start, "end": end} for start, end in sorted({
                (hit["start"], hit["end"]) for hit in hits if hit["field"] == field
            })]
            for field in fields
        }
        hit_fields = [field for field in fields if highlights[field]]
        return MatchReason(
            filter_id=flt.id, filter_name=flt.name, kind="keyword",
            details={
                "matched": matched,
                "in": ", ".join(hit_fields),
                "criteria": {
                    "any": [t.label for t in any_terms],
                    "all": [t.label for t in all_terms],
                    "none": [t.label for t in none_terms],
                },
                "absent": [t.label for t in none_terms],
                "hits": hits,
                "highlights": highlights,
            },
        )

    def match_many(self, articles: Sequence[Article], flt: Filter) -> list[Optional[MatchReason]]:
        """Convenience method mirroring SemanticEngine.match_many()."""
        return [self.match(article, flt) for article in articles]
