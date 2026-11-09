
"""Role 1 tests: full matching behavior without network or a model download.

Unit tests use an injected miniature morphology dictionary to exercise the
engine independently of the installation. The real pymorphy3 smoke tests at
the end run automatically when the package is installed.
"""
import importlib.util
import json

import pytest

from app.contracts import Article, Filter, MatchReason
from app.engines.keyword import KeywordEngine, KeywordFilterError, clean_text


INFLECTIONS = {
    "банки": "банк", "банков": "банк", "банком": "банк", "банка": "банк",
    "кредитам": "кредит", "кредиты": "кредит", "кредитов": "кредит",
    "россии": "россия", "россий": "россия", "нефти": "нефть",
    "искусственного": "искусственный", "интеллекта": "интеллект",
    "машинного": "машинный", "машинное": "машинный", "обучения": "обучение",
    "новые": "новый", "новых": "новый", "системы": "система",
    "ставки": "ставка", "ставок": "ставка", "цен": "цена",
    "продаж": "продажа", "санкций": "санкция",
}


def tiny_morph(word):
    return INFLECTIONS.get(word, word)


@pytest.fixture
def engine():
    return KeywordEngine(lemmatizer=tiny_morph)


def article(title, summary="", *, id=1):
    return Article(id=id, feed_id=17, guid=f"guid-{id}", title=title,
                   summary=summary, link="https://example.org/story")


def keyword(**params):
    return Filter(id=7, user_id=3, name="Экономика", kind="keyword", params=params)


@pytest.mark.parametrize("title, term, expected", [
    ("Банки снизили ставки", "банк", True),
    ("Вкладчики обсуждают банки", "БАНК", True),
    ("Банком принято решение", "банков", True),
    ("Банки снизили ставки", "нефть", False),
    ("Банкир открыл компанию", "банк", False),
    ("Супербанк выпустил отчёт", "банк", False),
    ("Ёжик нашёл ежа", "ежик", True),
    ("Курс вырос", "курс", True),
    ("Курсы выросли", "курс", False),
    ("NASA запустила аппарат", "nasa", True),
    ("ВВП вырос на 5%", "5", True),
])
def test_word_boundaries_case_and_morphology(engine, title, term, expected):
    assert (engine.match(article(title), keyword(any=[term])) is not None) == expected


@pytest.mark.parametrize("config, matched", [
    ({"any": ["нефть", "банк"]}, True),       # OR
    ({"any": ["нефть", "газ"]}, False),
    ({"all": ["банк", "кредит"]}, True),    # AND
    ({"all": ["банк", "нефть"]}, False),
    ({"none": ["реклама", "нефть"]}, True),  # NOT on its own
    ({"none": ["банк"]}, False),
    ({"any": ["банк", "нефть"], "all": ["кредит"], "none": ["реклама"]}, True),
    ({"any": ["банк"], "all": ["кредит"], "none": ["ставка"]}, False),
    ({"any": ["банк"], "all": ["нефть"], "none": ["реклама"]}, False),
])
def test_boolean_operators(engine, config, matched):
    news = article("Банки снизили ставки по кредитам")
    assert (engine.match(news, keyword(**config)) is not None) == matched


def test_fields_are_independent_and_default_is_both(engine):
    news = article("Погода на выходные", "Банки снизили ставки по кредитам")
    assert engine.match(news, keyword(any=["банк"])) is not None
    assert engine.match(news, keyword(any=["банк"], fields=["title"])) is None
    assert engine.match(news, keyword(any=["банк"], fields=["summary"])) is not None
    assert engine.match(news, keyword(all=["погода", "банк"])) is not None
    assert engine.match(news, keyword(any=["банк"], none=["погода"])) is None


@pytest.mark.parametrize("title, phrase, expected", [
    ("Новые системы машинного обучения", "машинное обучение", True),
    ("Машинное: обучение меняет мир", "машинное обучение", True),
    ("Машинное-обучение набирает обороты", "машинное обучение", True),
    ("Обучение машинного зрения", "машинное обучение", False),
    ("Машинное глубокое обучение", "машинное обучение", False),
    ("Банк и кредиты", "банк кредит", False),
    ("Банки кредитуют бизнес", "банк кредит", False),
    ("Искусственного интеллекта стало больше", "искусственный интеллект", True),
])
def test_phrases_require_consecutive_lemmas(engine, title, phrase, expected):
    assert (engine.match(article(title), keyword(any=[phrase])) is not None) == expected


def test_phrase_must_be_within_one_field(engine):
    news = article("Искусственный", "интеллект развивается")
    assert engine.match(news, keyword(any=["искусственный интеллект"])) is None


def test_html_entities_tags_and_hidden_content(engine):
    news = article("<b>Банки</b>&nbsp;снизили <i>ставки</i>",
                   "<script>нефть</script><p>Кредиты &amp; займы</p>")
    assert clean_text(news.title) == "Банки снизили ставки"
    assert clean_text(news.summary) == "Кредиты & займы"
    assert engine.match(news, keyword(all=["банк", "кредит"], none=["нефть"])) is not None
    assert engine.match(news, keyword(any=["нефть"])) is None
    assert clean_text("<style>банк</style><p>текст</p>") == "текст"
    assert clean_text(None) == ""


def test_explanation_contains_user_filter_and_exact_highlight_spans(engine):
    news = article("🔥 Банки снизили ставки", "Кредиты для бизнеса")
    result = engine.match(news, keyword(any=["банк", "нефть"], all=["кредит"], none=["реклама"]))
    assert isinstance(result, MatchReason)
    assert (result.filter_id, result.filter_name, result.kind) == (7, "Экономика", "keyword")
    details = result.details
    assert details["matched"] == ["банк", "кредит"]
    assert details["in"] == "title, summary"
    assert details["criteria"] == {"any": ["банк", "нефть"], "all": ["кредит"], "none": ["реклама"]}
    assert details["absent"] == ["реклама"]
    assert {hit["rule"] for hit in details["hits"]} == {"any", "all"}
    for hit in details["hits"]:
        displayed_field = clean_text(getattr(news, hit["field"]))
        assert displayed_field[hit["start"]:hit["end"]] == hit["text"]
    assert any(hit["text"] == "Банки" and hit["start"] == 2 for hit in details["hits"])
    assert any(hit["text"] == "Кредиты" for hit in details["hits"])
    json.dumps(details, ensure_ascii=False)  # safe for JSON API


def test_repeated_term_is_highlighted_twice_but_matched_once(engine):
    result = engine.match(article("Банк и банк открылись"), keyword(any=["банк", "банки"]))
    assert result.details["matched"] == ["банк"]  # equivalent lemmas are deduplicated
    assert len(result.details["highlights"]["title"]) == 2
    assert [hit["text"] for hit in result.details["hits"]] == ["Банк", "банк"]


def test_term_in_any_and_all_does_not_duplicate_highlight(engine):
    result = engine.match(article("Банки"), keyword(any=["банк"], all=["банки"]))
    assert len(result.details["hits"]) == 2
    assert len(result.details["highlights"]["title"]) == 1


def test_negative_only_filter_has_a_technical_reason(engine):
    result = engine.match(article("Объявили результаты"), keyword(none=["реклама"]))
    assert result is not None
    assert result.details["matched"] == []
    assert result.details["in"] == ""
    assert result.details["absent"] == ["реклама"]
    assert result.details["highlights"] == {"title": [], "summary": []}


def test_string_fields_and_comma_separated_keywords(engine):
    result = engine.match(article("Банки снизили ставки"), keyword(any="нефть, банк", fields="title"))
    assert result.details["matched"] == ["банк"]
    assert set(result.details["highlights"]) == {"title"}


@pytest.mark.parametrize("params", [
    {}, {"any": []}, {"all": ["  "]}, {"any": ["!!!"]}, {"any": None},
])
def test_empty_rules_never_match_all_news(engine, params):
    assert engine.match(article("Любая статья"), keyword(**params)) is None


@pytest.mark.parametrize("params", [
    {"any": 12}, {"all": [12]}, {"none": {"bank": True}},
    {"any": ["банк"], "fields": []},
    {"any": ["банк"], "fields": "body"},
    {"any": ["банк"], "fields": 123},
    {"any": ["банк"], "fields": ["title", "unknown"]},
])
def test_invalid_filter_returns_descriptive_error(engine, params):
    with pytest.raises(KeywordFilterError):
        engine.match(article("Банк"), keyword(**params))


def test_topic_filter_is_ignored_and_no_params_are_mutated(engine):
    params = {"any": ["банк"], "fields": ["title"]}
    flt = keyword(**params)
    assert engine.match(article("Банк"), flt) is not None
    assert flt.params == params
    topic = Filter(id=8, user_id=3, name="Тема", kind="topic", params={"description": "банк"})
    assert engine.match(article("Банк"), topic) is None


def test_separate_users_receive_same_result_for_same_filter(engine):
    a = article("Банки")
    one = keyword(any=["банк"])
    two = Filter(id=31, user_id=99, name="Мои финансы", kind="keyword", params={"any": ["банк"]})
    r1 = engine.match(a, one)
    r2 = engine.match(a, two)
    assert r1 is not None and r2 is not None
    assert r1.filter_id == 7 and r2.filter_id == 31
    assert r1.filter_name != r2.filter_name


def test_match_many_returns_results_in_article_order(engine):
    news = [article("Банки", id=1), article("О погоде", id=2), article("Кредиты", id=3)]
    assert [r is not None for r in engine.match_many(news, keyword(any=["банк", "кредит"]))] == [True, False, True]


def test_lemma_cache_reuses_morphology_results():
    calls = []

    def counting_lemma(word):
        calls.append(word)
        return tiny_morph(word)

    engine = KeywordEngine(lemmatizer=counting_lemma)
    f = keyword(any=["банк"])
    a = article("Банки снизили ставки")
    engine.match(a, f)
    count = len(calls)
    engine.match(a, f)
    assert len(calls) == count


@pytest.mark.skipif(importlib.util.find_spec("pymorphy3") is None,
                    reason="pymorphy3 не установлен в среде выполнения")
def test_real_pymorphy3_morphology():
    engine = KeywordEngine()
    assert engine.match(article("Банки России расширили кредитование"),
                        keyword(all=["банк", "Россия"])) is not None
    assert engine.match(article("Машинного обучения стало больше"),
                        keyword(any=["машинное обучение"])) is not None
    assert engine.match(article("Банковский сектор вырос"),
                        keyword(any=["банк"])) is None
