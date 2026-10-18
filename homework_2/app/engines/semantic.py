"""Семантический фильтр: подходит ли новость к теме по смыслу, а не по словам.

Статья и тема превращаются в векторы локальной моделью, сходство — косинусное. Вектор статьи
считается один раз (по хешу текста) и кэшируется, поэтому повторные проверки дешёвые.
Модуль не знает о БД и сети: на входе Article и Filter, на выходе MatchReason или None.

    engine = SemanticEngine()
    engine.index(articles)                 # посчитать векторы пакетами (необязательно)
    reason = engine.match(article, flt)    # MatchReason(details={"score": ..., "threshold": ...}) или None

Тема задаётся описанием (`description`) и/или примерами (`examples`). По умолчанию (`aggregate: "mean"`)
фразы усредняются в один вектор темы: это устойчивее одной неудачной фразы; `aggregate: "max"` берёт
лучшую фразу (тема как «или»). Самая близкая фраза попадает в объяснение. Порог — `threshold` фильтра.
"""
from __future__ import annotations

import hashlib
import html
import re
import threading
from collections import OrderedDict
from typing import Callable, Iterable, Optional, Sequence

import numpy as np

from app.contracts import Article, Filter, MatchReason

DEFAULT_MODEL = "cointegrated/rubert-tiny2"
# Подобран на размеченном наборе data/semantic_dev.json (scripts/bench_semantic.py). Шкала сходства у
# модели сжата: при 0.44 средний F1 0.59, при 0.52 уже 0.25, поэтому порог нужно настраивать по каждой теме.
DEFAULT_THRESHOLD = 0.45

Encoder = Callable[[Sequence[str]], np.ndarray]

_TAGS = re.compile(r"<[^>]+>")
_SPACES = re.compile(r"\s+")


class SemanticError(RuntimeError):
    """Модель не загрузилась или фильтр заполнен неверно; сервис не должен падать, а показать причину."""


def clean_text(text: Optional[str]) -> str:
    """HTML, сущности и лишние пробелы убираются; роль 3 тоже чистит, но на всякий случай."""
    if not text:
        return ""
    return _SPACES.sub(" ", html.unescape(_TAGS.sub(" ", text))).strip()


def article_text(article: Article, max_chars: int = 1000) -> str:
    title, summary = clean_text(article.title), clean_text(article.summary)
    text = f"{title}. {summary}" if title and summary else title or summary
    return text[:max_chars]


def to_bytes(vector: np.ndarray) -> bytes:
    """Вектор -> байты для хранения в БД (float32)."""
    return np.asarray(vector, dtype=np.float32).tobytes()


def from_bytes(data: bytes) -> np.ndarray:
    return np.frombuffer(data, dtype=np.float32)


def _key(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


class SemanticEngine:
    def __init__(
        self,
        model_name: str = DEFAULT_MODEL,
        *,
        encoder: Optional[Encoder] = None,
        query_prefix: str = "",
        passage_prefix: str = "",
        max_chars: int = 1000,
        batch_size: int = 32,
        cache_size: int = 50_000,
        device: str = "cpu",
    ) -> None:
        """encoder можно подменить (тесты, другая модель); он возвращает нормализованные векторы (n, d).

        query_prefix и passage_prefix нужны моделям семейства e5 ("query: ", "passage: ").
        """
        self.model_name = model_name
        self.query_prefix = query_prefix
        self.passage_prefix = passage_prefix
        self.max_chars = max_chars
        self.batch_size = batch_size
        self.device = device
        self._encoder = encoder
        self._lock = threading.Lock()
        self._cache: "OrderedDict[str, np.ndarray]" = OrderedDict()
        self._cache_size = cache_size

    # ------------------------------------------------------------------ модель
    def _load(self) -> Encoder:
        with self._lock:
            if self._encoder is None:
                try:
                    from sentence_transformers import SentenceTransformer
                    model = SentenceTransformer(self.model_name, device=self.device)
                except Exception as exc:  # нет модели, нет сети, нет библиотеки
                    raise SemanticError(f"не удалось загрузить модель {self.model_name}: {exc}") from exc
                self._encoder = lambda texts: model.encode(
                    list(texts), batch_size=self.batch_size, normalize_embeddings=True,
                    show_progress_bar=False, convert_to_numpy=True)
            return self._encoder

    def embed(self, texts: Sequence[str]) -> np.ndarray:
        """Нормализованные векторы float32 формы (n, d); пустой вход даёт пустой массив."""
        if not texts:
            return np.zeros((0, 0), dtype=np.float32)
        vectors = np.asarray(self._load()(list(texts)), dtype=np.float32)
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        return vectors / np.where(norms == 0, 1.0, norms)

    # ------------------------------------------------------------------- кэш
    def _remember(self, key: str, vector: np.ndarray) -> None:
        self._cache[key] = vector
        self._cache.move_to_end(key)
        while len(self._cache) > self._cache_size:
            self._cache.popitem(last=False)

    def _vectors(self, texts: Sequence[str], prefix: str) -> np.ndarray:
        """Векторы текстов; недостающие считаются одним пакетом, остальные берутся из кэша."""
        if not texts:
            return np.zeros((0, 0), dtype=np.float32)
        keys = [_key(prefix + t) for t in texts]
        found = {k: self._cache[k] for k in keys if k in self._cache}
        unique: dict[str, str] = {}
        for k, t in zip(keys, texts):
            if k not in found:
                unique.setdefault(k, prefix + t)
        if unique:
            for k, v in zip(unique, self.embed(list(unique.values()))):
                found[k] = v
                self._remember(k, v)   # кэш ограничен: результат берём из found, а не из кэша
        return np.stack([found[k] for k in keys])

    def index(self, articles: Iterable[Article]) -> int:
        """Посчитать и закэшировать векторы статей пакетами; возвращает число новых векторов."""
        texts = [article_text(a, self.max_chars) for a in articles]
        before = len(self._cache)
        self._vectors([t for t in texts if t], self.passage_prefix)
        return len(self._cache) - before

    def article_vector(self, article: Article) -> Optional[np.ndarray]:
        """Вектор статьи для хранения в БД (to_bytes/from_bytes); None, если текста нет."""
        text = article_text(article, self.max_chars)
        return self._vectors([text], self.passage_prefix)[0] if text else None

    def preload(self, article: Article, vector: np.ndarray) -> None:
        """Положить в кэш вектор, прочитанный из БД, чтобы не считать его заново."""
        text = article_text(article, self.max_chars)
        if text:
            self._remember(_key(self.passage_prefix + text), np.asarray(vector, dtype=np.float32))

    # ------------------------------------------------------------------ тема
    @staticmethod
    def topic_phrases(flt: Filter) -> list[str]:
        params = flt.params or {}
        phrases = []
        description = params.get("description")
        if isinstance(description, str) and description.strip():
            phrases.append(clean_text(description))
        examples = params.get("examples") or []
        if isinstance(examples, str):
            examples = [examples]
        phrases += [clean_text(e) for e in examples if isinstance(e, str) and e.strip()]
        return list(dict.fromkeys(phrases))

    @staticmethod
    def threshold(flt: Filter) -> float:
        value = (flt.params or {}).get("threshold", DEFAULT_THRESHOLD)
        try:
            value = float(value)
        except (TypeError, ValueError) as exc:
            raise SemanticError(f"порог темы «{flt.name}» не число: {value!r}") from exc
        if not -1.0 <= value <= 1.0:
            raise SemanticError(f"порог темы «{flt.name}» вне диапазона [-1, 1]: {value}")
        return value

    def score(self, article: Article, flt: Filter) -> Optional[tuple[float, str]]:
        """(оценка, лучшая фраза темы) или None, если у статьи нет текста или у темы нет фраз."""
        text = article_text(article, self.max_chars)
        phrases = self.topic_phrases(flt)
        if not text or not phrases:
            return None
        article_vec = self._vectors([text], self.passage_prefix)[0]
        phrase_vecs = self._vectors(phrases, self.query_prefix)
        similarities = phrase_vecs @ article_vec
        best = int(np.argmax(similarities))
        aggregate = (flt.params or {}).get("aggregate", "mean")
        if aggregate == "max":
            return float(similarities[best]), phrases[best]
        if aggregate != "mean":
            raise SemanticError(f"aggregate темы «{flt.name}» должен быть mean или max, получено {aggregate!r}")
        centroid = phrase_vecs.mean(axis=0)
        norm = np.linalg.norm(centroid)
        score = float(centroid @ article_vec / norm) if norm else 0.0
        return score, phrases[best]

    def match(self, article: Article, flt: Filter) -> Optional[MatchReason]:
        if flt.kind != "topic":
            return None
        result = self.score(article, flt)
        if result is None:
            return None
        score, phrase = result
        threshold = self.threshold(flt)
        if score < threshold:
            return None
        return MatchReason(
            filter_id=flt.id, filter_name=flt.name, kind="topic",
            details={"score": round(score, 4), "threshold": threshold, "best_phrase": phrase},
        )

    def rank(self, articles: Sequence[Article], flt: Filter) -> list[tuple[Article, float]]:
        """Статьи по убыванию сходства с темой, без порога: для предпросмотра и подбора порога в интерфейсе."""
        self.index(articles)
        scored = [(a, r[0]) for a in articles if (r := self.score(a, flt)) is not None]
        return sorted(scored, key=lambda pair: pair[1], reverse=True)

    def match_many(self, articles: Sequence[Article], flt: Filter) -> list[Optional[MatchReason]]:
        """Те же проверки для списка статей; недостающие векторы статей считаются одним пакетом."""
        self.index(articles)
        return [self.match(a, flt) for a in articles]
