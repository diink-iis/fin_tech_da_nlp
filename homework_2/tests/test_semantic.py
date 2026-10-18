"""Тесты семантического движка. Основные работают без модели: на подставном кодировщике
(хешированный мешок слов), реальная модель проверяется отдельно — RUN_MODEL_TESTS=1 pytest."""
import hashlib
import os
import re
import sys

import numpy as np
import pytest

from app.contracts import Article, Filter
from app.engines.semantic import (DEFAULT_THRESHOLD, SemanticEngine, SemanticError, article_text, clean_text,
                                  from_bytes, to_bytes)

DIM = 256


def fake_encoder(texts):
    """Мешок слов в хеш-пространстве: общие слова дают косинусное сходство, как у настоящей модели."""
    out = np.zeros((len(texts), DIM), dtype=np.float32)
    for row, text in enumerate(texts):
        for word in re.findall(r"\w+", text.lower()):
            out[row, int(hashlib.md5(word.encode()).hexdigest(), 16) % DIM] += 1.0
    norms = np.linalg.norm(out, axis=1, keepdims=True)
    return out / np.where(norms == 0, 1, norms)


class CountingEncoder:
    def __init__(self):
        self.calls = []

    def __call__(self, texts):
        self.calls.append(list(texts))
        return fake_encoder(texts)

    @property
    def texts_encoded(self):
        return sum(len(c) for c in self.calls)


def art(i, title, summary=""):
    return Article(id=i, feed_id=1, guid=str(i), title=title, summary=summary, link="")


def topic(description="футбол матч клуб", threshold=0.3, **extra):
    return Filter(id=7, user_id=1, name="Спорт", kind="topic",
                  params={"description": description, "threshold": threshold, **extra})


SPORT = art(1, "Динамо выиграло матч", "Футбольный клуб одержал победу в матче")
POLITICS = art(2, "Президент провёл переговоры", "Министры обсудили санкции")


@pytest.fixture
def engine():
    return SemanticEngine(encoder=fake_encoder)


# ------------------------------------------------------------------ очистка текста
def test_clean_text_removes_html_entities_and_spaces():
    assert clean_text("<p>Курс&nbsp;доллара &amp;  нефть</p>\n") == "Курс доллара & нефть"
    assert clean_text(None) == "" and clean_text("") == ""


def test_article_text_joins_title_and_summary_and_truncates():
    assert article_text(art(1, "Заголовок", "Анонс")) == "Заголовок. Анонс"
    assert article_text(art(1, "Только заголовок", "")) == "Только заголовок"
    assert len(article_text(art(1, "а" * 5000), max_chars=100)) == 100


def test_vector_bytes_roundtrip():
    v = fake_encoder(["тест вектора"])[0]
    assert np.allclose(from_bytes(to_bytes(v)), v)


# ------------------------------------------------------------------------ match
def test_match_above_threshold_has_explanation(engine):
    reason = engine.match(SPORT, topic())
    assert reason is not None
    assert (reason.filter_id, reason.filter_name, reason.kind) == (7, "Спорт", "topic")
    assert set(reason.details) == {"score", "threshold", "best_phrase"}
    assert reason.details["score"] >= reason.details["threshold"] == 0.3
    assert reason.details["best_phrase"] == "футбол матч клуб"


def test_match_below_threshold_is_none(engine):
    assert engine.match(POLITICS, topic()) is None


def test_threshold_is_the_boundary(engine):
    score = engine.score(SPORT, topic())[0]
    assert engine.match(SPORT, topic(threshold=score - 1e-6)) is not None
    assert engine.match(SPORT, topic(threshold=score + 1e-3)) is None


def test_default_threshold_used_when_missing(engine):
    flt = Filter(id=1, user_id=1, name="Т", kind="topic",
                 params={"description": "Динамо выиграло матч футбольный клуб одержал победу в матче"})
    reason = engine.match(SPORT, flt)
    assert reason is not None and reason.details["threshold"] == DEFAULT_THRESHOLD


def test_keyword_filter_is_ignored(engine):
    flt = Filter(id=1, user_id=1, name="К", kind="keyword", params={"any": ["матч"]})
    assert engine.match(SPORT, flt) is None


@pytest.mark.parametrize("flt_params", [{}, {"description": "   "}, {"examples": []}, {"description": None}])
def test_topic_without_phrases_never_matches(engine, flt_params):
    flt = Filter(id=1, user_id=1, name="Т", kind="topic", params=flt_params)
    assert engine.match(SPORT, flt) is None


def test_article_without_text_never_matches(engine):
    assert engine.match(art(3, "", ""), topic()) is None
    assert engine.match(art(3, "  ", "<br>"), topic()) is None


@pytest.mark.parametrize("bad", ["много", 2.0, -1.5, None])
def test_invalid_threshold_raises(engine, bad):
    flt = Filter(id=1, user_id=1, name="Т", kind="topic", params={"description": "футбол", "threshold": bad})
    with pytest.raises(SemanticError):
        engine.match(SPORT, flt)


def test_invalid_aggregate_raises(engine):
    with pytest.raises(SemanticError):
        engine.match(SPORT, topic(aggregate="median"))


def test_examples_string_is_accepted(engine):
    flt = Filter(id=1, user_id=1, name="Т", kind="topic", params={"examples": "футбольный клуб матч", "threshold": 0.3})
    assert engine.match(SPORT, flt) is not None


# ------------------------------------------------------------- несколько фраз
def test_max_aggregate_matches_on_best_phrase_only(engine):
    flt = topic(description="биткоин криптовалюта", threshold=0.5, examples=["футбольный матч клуб победа"],
                aggregate="max")
    reason = engine.match(SPORT, flt)
    assert reason is not None and reason.details["best_phrase"] == "футбольный матч клуб победа"


def test_mean_aggregate_dilutes_unrelated_phrase(engine):
    base = dict(description="биткоин криптовалюта", threshold=0.5, examples=["футбольный матч клуб победа"])
    assert engine.match(SPORT, topic(**base, aggregate="max")) is not None
    assert engine.match(SPORT, topic(**base)) is None  # по умолчанию усреднение


def test_duplicate_phrases_are_collapsed(engine):
    flt = topic(description="футбол", examples=["футбол", "футбол"])
    assert SemanticEngine.topic_phrases(flt) == ["футбол"]


# --------------------------------------------------------------- кэш и пакеты
def test_article_vector_is_computed_once():
    enc = CountingEncoder()
    engine = SemanticEngine(encoder=enc)
    flt = topic()
    for _ in range(5):
        engine.match(SPORT, flt)
    # один раз статья и один раз фраза темы
    assert enc.texts_encoded == 2


def test_index_embeds_in_a_single_batch_and_skips_known():
    enc = CountingEncoder()
    engine = SemanticEngine(encoder=enc)
    articles = [art(i, f"Новость номер {i}", "текст") for i in range(20)]
    assert engine.index(articles) == 20 and len(enc.calls) == 1
    assert engine.index(articles) == 0 and len(enc.calls) == 1


def test_index_ignores_empty_articles_and_duplicates():
    enc = CountingEncoder()
    engine = SemanticEngine(encoder=enc)
    added = engine.index([art(1, "Один"), art(2, "Один"), art(3, "")])
    assert added == 1 and enc.texts_encoded == 1


def test_preload_avoids_recomputation():
    enc = CountingEncoder()
    engine = SemanticEngine(encoder=enc)
    engine.preload(SPORT, fake_encoder([article_text(SPORT)])[0])
    engine.match(SPORT, topic())
    assert enc.texts_encoded == 1  # только фраза темы


def test_article_vector_for_storage(engine):
    vec = engine.article_vector(SPORT)
    assert vec.shape == (DIM,) and np.isclose(np.linalg.norm(vec), 1.0)
    assert engine.article_vector(art(9, "", "")) is None


def test_cache_is_bounded():
    engine = SemanticEngine(encoder=fake_encoder, cache_size=10)
    engine.index([art(i, f"Статья {i}", "текст") for i in range(100)])
    assert len(engine._cache) == 10


def test_match_many_matches_individual_results(engine):
    flt = topic()
    articles = [SPORT, POLITICS]
    assert [r is not None for r in engine.match_many(articles, flt)] == [True, False]


def test_rank_orders_by_score_without_threshold(engine):
    ranked = engine.rank([POLITICS, SPORT, art(5, "")], topic(threshold=0.99))
    assert [a.id for a, _ in ranked] == [1, 2]  # без порога; статья без текста не попадает
    assert ranked[0][1] >= ranked[1][1]


# ---------------------------------------------------------------- ошибки модели
def test_model_load_failure_is_a_clear_error(monkeypatch):
    broken = SemanticEngine("no/such-model")
    monkeypatch.setitem(sys.modules, "sentence_transformers", None)  # импорт даст ImportError
    with pytest.raises(SemanticError, match="не удалось загрузить модель"):
        broken.match(SPORT, topic())


def test_empty_input_embeds_to_empty_array(engine):
    assert engine.embed([]).shape[0] == 0


# ----------------------------------------------------- реальная модель (по запросу)
real = pytest.mark.skipif(os.getenv("RUN_MODEL_TESTS") != "1",
                          reason="нужна загруженная модель, запуск: RUN_MODEL_TESTS=1 pytest")


@real
def test_real_model_topic_without_shared_words():
    engine = SemanticEngine()
    # порог 0.40: у rubert-tiny2 шкала сжата, для спорта лучший порог на размеченном наборе около 0.41
    flt = Filter(id=1, user_id=1, name="Спорт", kind="topic", params={
        "description": "Спорт: футбол, хоккей, теннис, соревнования, матчи, клубы, спортсмены", "threshold": 0.40})
    sport = art(1, "ЦСКА уступил «Динамо» в московском дерби", "Красно-синие впервые проиграли в сезоне")
    other = art(2, "Правительство утвердило проект бюджета на три года", "Расходы на социальную сферу вырастут")
    assert engine.match(sport, flt) is not None
    assert engine.match(other, flt) is None


@real
def test_real_model_scores_are_in_range_and_stable():
    engine = SemanticEngine()
    a = engine.article_vector(SPORT)
    assert np.isclose(np.linalg.norm(a), 1.0, atol=1e-4)
    assert np.allclose(a, SemanticEngine().article_vector(SPORT), atol=1e-4)
