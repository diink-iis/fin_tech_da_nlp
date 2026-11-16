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
import re
import threading
from collections import OrderedDict
from html.parser import HTMLParser
from typing import Callable, Iterable, Optional, Sequence

import numpy as np

from app.contracts import Article, Filter, MatchReason

DEFAULT_MODEL = "cointegrated/rubert-tiny2"
# Подобран на размеченном наборе data/semantic_dev.json (scripts/bench_semantic.py). Шкала сходства у
# модели сжата: при 0.44 средний F1 0.59, при 0.52 уже 0.25, поэтому порог нужно настраивать по каждой теме.
DEFAULT_THRESHOLD = 0.45

Encoder = Callable[[Sequence[str]], np.ndarray]

_SPACES = re.compile(r"\s+")
_BLOCK_TAGS = frozenset({
    "address", "article", "blockquote", "br", "div", "h1", "h2", "h3", "h4", "h5", "h6",
    "hr", "li", "ol", "p", "section", "table", "td", "th", "tr", "ul",
})


class SemanticError(RuntimeError):
    """Ошибка модели, вектора или настроек; вызывающий код может показать понятную причину."""


class _PlainText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in {"script", "style"}:
            self.hidden.append(tag)
        elif tag in _BLOCK_TAGS and not self.hidden:
            self.parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"} and tag in self.hidden:
            self.hidden.remove(tag)
        elif tag in _BLOCK_TAGS and not self.hidden:
            self.parts.append(" ")

    def handle_startendtag(self, tag: str, attrs) -> None:
        if tag in _BLOCK_TAGS and not self.hidden:
            self.parts.append(" ")

    def handle_data(self, data: str) -> None:
        if not self.hidden:
            self.parts.append(data)


def clean_text(text: Optional[str]) -> str:
    """HTML, сущности и лишние пробелы убираются; роль 3 тоже чистит, но на всякий случай."""
    if text is None:
        return ""
    if not isinstance(text, str):
        raise SemanticError("текст новости или темы должен быть строкой")
    parser = _PlainText()
    parser.feed(text)
    parser.close()
    return _SPACES.sub(" ", "".join(parser.parts)).strip()


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
        for name, value, minimum in (("max_chars", max_chars, 1), ("batch_size", batch_size, 1),
                                     ("cache_size", cache_size, 0)):
            if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
                raise SemanticError(f"{name} должен быть целым числом не меньше {minimum}")
        self.model_name = model_name
        self.query_prefix = query_prefix
        self.passage_prefix = passage_prefix
        self.max_chars = max_chars
        self.batch_size = batch_size
        self.device = device
        self._encoder = encoder
        # FastAPI can share the engine between worker threads. Keep cache lookup,
        # missing-vector computation and eviction atomic, including nested _load().
        self._lock = threading.RLock()
        self._dimension: Optional[int] = None
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
        with self._lock:
            encoder = self._load()
            try:
                raw = encoder(list(texts))
            except SemanticError:
                raise
            except Exception as exc:
                raise SemanticError(f"не удалось вычислить эмбеддинги моделью {self.model_name}: {exc}") from exc
            return self._normalize_vectors(raw, len(texts))

    def _normalize_vectors(self, raw, rows: int) -> np.ndarray:
        """Validate encoder/preloaded vectors before they can enter the cache. Caller holds _lock."""
        try:
            with np.errstate(over="ignore", invalid="ignore"):
                vectors = np.asarray(raw, dtype=np.float32)
        except (TypeError, ValueError, OverflowError) as exc:
            raise SemanticError("эмбеддинги должны быть числовыми векторами float32") from exc
        if vectors.ndim != 2 or vectors.shape[0] != rows or vectors.shape[1] == 0:
            raise SemanticError(f"неверная форма эмбеддингов: ожидается ({rows}, d), d > 0; получено {vectors.shape}")
        if not np.isfinite(vectors).all():
            raise SemanticError("эмбеддинги содержат NaN или бесконечность")
        dimension = vectors.shape[1]
        if self._dimension is not None and dimension != self._dimension:
            raise SemanticError(f"несовместимая размерность эмбеддингов: {dimension}, ожидается {self._dimension}")
        # float64 norms avoid overflow for large but finite float32 inputs.
        norms = np.linalg.norm(vectors.astype(np.float64), axis=1, keepdims=True)
        normalized = (vectors / np.where(norms == 0, 1.0, norms)).astype(np.float32)
        self._dimension = dimension
        return normalized

    # ------------------------------------------------------------------- кэш
    def _remember(self, key: str, vector: np.ndarray) -> None:
        with self._lock:
            self._cache[key] = vector
            self._cache.move_to_end(key)
            while len(self._cache) > self._cache_size:
                self._cache.popitem(last=False)

    def _vectors(self, texts: Sequence[str], prefix: str) -> np.ndarray:
        """Векторы текстов; недостающие считаются одним пакетом, остальные берутся из кэша."""
        return self._cached_vectors(texts, prefix)[0]

    def _cached_vectors(self, texts: Sequence[str], prefix: str) -> tuple[np.ndarray, int]:
        """Return vectors and the number computed, regardless of cache eviction."""
        if not texts:
            return np.zeros((0, 0), dtype=np.float32), 0
        with self._lock:
            keys = [_key(prefix + t) for t in texts]
            found: dict[str, np.ndarray] = {}
            unique: dict[str, str] = {}
            for k, t in zip(keys, texts):
                if k in self._cache:
                    found[k] = self._cache[k]
                    self._cache.move_to_end(k)
                else:
                    unique.setdefault(k, prefix + t)
            if unique:
                for k, v in zip(unique, self.embed(list(unique.values()))):
                    found[k] = v
                    self._remember(k, v)   # результат берём из found: кэш мог уже вытеснить вектор
            return np.stack([found[k] for k in keys]), len(unique)

    def index(self, articles: Iterable[Article]) -> int:
        """Число вычисленных уникальных векторов, включая впоследствии вытесненные из LRU-кэша."""
        texts = [article_text(a, self.max_chars) for a in articles]
        return self._cached_vectors([t for t in texts if t], self.passage_prefix)[1]

    def article_vector(self, article: Article) -> Optional[np.ndarray]:
        """Вектор статьи для хранения в БД (to_bytes/from_bytes); None, если текста нет."""
        text = article_text(article, self.max_chars)
        return self._vectors([text], self.passage_prefix)[0] if text else None

    def preload(self, article: Article, vector: np.ndarray) -> None:
        """Проверить, нормализовать и закэшировать вектор из БД, чтобы не считать его заново."""
        text = article_text(article, self.max_chars)
        if text:
            with self._lock:
                try:
                    raw = np.asarray(vector, dtype=np.float32)
                except (TypeError, ValueError, OverflowError) as exc:
                    raise SemanticError("сохранённый эмбеддинг должен быть числовым вектором") from exc
                if raw.ndim != 1:
                    raise SemanticError("сохранённый эмбеддинг должен быть одномерным вектором")
                normalized = self._normalize_vectors(raw[np.newaxis, :], 1)[0]
                self._remember(_key(self.passage_prefix + text), normalized)

    # ------------------------------------------------------------------ тема
    @staticmethod
    def _params(flt: Filter) -> dict:
        if flt.params is None:
            return {}
        if not isinstance(flt.params, dict):
            raise SemanticError("params темы должен быть объектом")
        return flt.params

    @staticmethod
    def topic_phrases(flt: Filter) -> list[str]:
        params = SemanticEngine._params(flt)
        phrases = []
        description = params.get("description")
        if description is not None and not isinstance(description, str):
            raise SemanticError("description темы должен быть строкой")
        if description:
            phrases.append(clean_text(description))
        examples = params.get("examples")
        if examples is None:
            examples = []
        if isinstance(examples, str):
            examples = [examples]
        if not isinstance(examples, (list, tuple)) or any(not isinstance(e, str) for e in examples):
            raise SemanticError("examples темы должен быть строкой или списком строк")
        phrases += [clean_text(e) for e in examples]
        return list(dict.fromkeys(p for p in phrases if p))

    @staticmethod
    def _aggregate(flt: Filter) -> str:
        aggregate = SemanticEngine._params(flt).get("aggregate", "mean")
        if aggregate not in ("mean", "max"):
            raise SemanticError(f"aggregate темы «{flt.name}» должен быть mean или max, получено {aggregate!r}")
        return aggregate

    @staticmethod
    def threshold(flt: Filter) -> float:
        value = SemanticEngine._params(flt).get("threshold", DEFAULT_THRESHOLD)
        try:
            if isinstance(value, bool):
                raise ValueError("логическое значение вместо числа")
            value = float(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise SemanticError(f"порог темы «{flt.name}» не число: {value!r}") from exc
        if not -1.0 <= value <= 1.0:
            raise SemanticError(f"порог темы «{flt.name}» вне диапазона [-1, 1]: {value}")
        return value

    def score(self, article: Article, flt: Filter) -> Optional[tuple[float, str]]:
        """(оценка, лучшая фраза темы) или None, если у статьи нет текста или у темы нет фраз."""
        if flt.kind != "topic":
            return None
        aggregate = self._aggregate(flt)
        text = article_text(article, self.max_chars)
        phrases = self.topic_phrases(flt)
        if not text or not phrases:
            return None
        article_vec = self._vectors([text], self.passage_prefix)[0]
        phrase_vecs = self._vectors(phrases, self.query_prefix)
        similarities = phrase_vecs @ article_vec
        best = int(np.argmax(similarities))
        if aggregate == "max":
            return float(np.clip(similarities[best], -1.0, 1.0)), phrases[best]
        centroid = phrase_vecs.mean(axis=0)
        norm = np.linalg.norm(centroid)
        score = float(centroid @ article_vec / norm) if norm else 0.0
        return float(np.clip(score, -1.0, 1.0)), phrases[best]

    def match(self, article: Article, flt: Filter) -> Optional[MatchReason]:
        if flt.kind != "topic":
            return None
        threshold = self.threshold(flt)
        result = self.score(article, flt)
        if result is None:
            return None
        score, phrase = result
        if score < threshold:
            return None
        return MatchReason(
            filter_id=flt.id, filter_name=flt.name, kind="topic",
            details={"score": round(score, 4), "threshold": threshold, "best_phrase": phrase},
        )

    def rank(self, articles: Sequence[Article], flt: Filter) -> list[tuple[Article, float]]:
        """Статьи по убыванию сходства с темой, без порога: для предпросмотра и подбора порога в интерфейсе."""
        if flt.kind != "topic":
            return []
        self._aggregate(flt)
        if not self.topic_phrases(flt):
            return []
        self.index(articles)
        scored = [(a, r[0]) for a in articles if (r := self.score(a, flt)) is not None]
        return sorted(scored, key=lambda pair: pair[1], reverse=True)

    def match_many(self, articles: Sequence[Article], flt: Filter) -> list[Optional[MatchReason]]:
        """Те же проверки для списка статей; недостающие векторы статей считаются одним пакетом."""
        if flt.kind != "topic":
            return [None for _ in articles]
        self.threshold(flt)
        self._aggregate(flt)
        if not self.topic_phrases(flt):
            return [None for _ in articles]
        self.index(articles)
        return [self.match(a, flt) for a in articles]
