"""Regressions found while reviewing role 2; no network or model download."""
import numpy as np
import pytest

from app.contracts import Filter
from app.engines.semantic import SemanticEngine, SemanticError, article_text, clean_text
from tests.test_semantic import CountingEncoder, DIM, SPORT, art, fake_encoder, topic


def test_encoder_failure_keeps_cause_and_becomes_semantic_error():
    failure = RuntimeError("inference failed")

    def broken_encoder(texts):
        raise failure

    with pytest.raises(SemanticError) as caught:
        SemanticEngine(encoder=broken_encoder).match(SPORT, topic())
    assert caught.value.__cause__ is failure


@pytest.mark.parametrize("output", [
    np.ones(3),                       # one vector rather than a batch
    np.ones((2, 3)),                  # wrong number of input rows
    np.empty((1, 0)),                 # empty embedding dimension
    np.ones((1, 1, 3)),               # unexpected extra dimension
    [[float("nan"), 1.0]],
    [[float("inf"), 1.0]],
    [["not a number"]],
])
def test_invalid_encoder_output_is_a_semantic_error(output):
    engine = SemanticEngine(encoder=lambda texts: output)
    with pytest.raises(SemanticError):
        engine.embed(["article"])


def test_encoder_dimension_cannot_change_between_calls():
    outputs = iter([np.ones((1, 3)), np.ones((1, 4))])
    engine = SemanticEngine(encoder=lambda texts: next(outputs))
    engine.embed(["first"])
    with pytest.raises(SemanticError):
        engine.embed(["second"])


def test_failed_batch_does_not_cache_partial_results():
    attempts = []

    def encoder(texts):
        attempts.append(list(texts))
        return np.ones((1, DIM)) if len(attempts) == 1 else fake_encoder(texts)

    engine = SemanticEngine(encoder=encoder)
    articles = [art(1, "first"), art(2, "second")]
    with pytest.raises(SemanticError):
        engine.index(articles)
    assert engine.index(articles) == 2
    assert attempts == [["first", "second"], ["first", "second"]]


def test_index_counts_new_vectors_when_full_cache_evicts_another_article():
    encoder = CountingEncoder()
    engine = SemanticEngine(encoder=encoder, cache_size=1)
    assert engine.index([art(1, "first")]) == 1
    assert engine.index([art(2, "second")]) == 1
    assert engine.index([art(2, "second")]) == 0
    assert encoder.texts_encoded == 2


def test_index_counts_unique_uncached_texts_in_batch_larger_than_cache():
    encoder = CountingEncoder()
    engine = SemanticEngine(encoder=encoder, cache_size=1)
    assert engine.index([art(1, "first"), art(2, "first"), art(3, "second"), art(4, "")]) == 2
    assert encoder.calls == [["first", "second"]]


def test_cache_hit_preserves_recently_used_article():
    encoder = CountingEncoder()
    engine = SemanticEngine(encoder=encoder, cache_size=2)
    first, second, third = [art(i, f"article {i}") for i in range(3)]
    engine.index([first, second])
    engine.article_vector(first)  # first is now more recent than second
    engine.index([third])
    encoded = encoder.texts_encoded
    engine.article_vector(first)
    assert encoder.texts_encoded == encoded
    engine.article_vector(second)
    assert encoder.texts_encoded == encoded + 1


def test_preload_normalizes_vector_without_changing_input_or_reencoding_article():
    encoder = CountingEncoder()
    engine = SemanticEngine(encoder=encoder)
    vector = fake_encoder([article_text(SPORT)])[0] * 7
    original = vector.copy()
    engine.preload(SPORT, vector)
    restored = engine.article_vector(SPORT)
    assert np.isclose(np.linalg.norm(restored), 1.0)
    assert np.array_equal(vector, original)
    assert encoder.calls == []


@pytest.mark.parametrize("vector", [
    np.ones((1, DIM)), np.array([]), np.array([float("nan")]),
    np.array([float("inf")]), ["not a number"],
])
def test_preload_rejects_invalid_vectors(vector):
    with pytest.raises(SemanticError):
        SemanticEngine(encoder=fake_encoder).preload(SPORT, vector)


def test_preload_rejects_dimension_inconsistent_with_encoder():
    engine = SemanticEngine(encoder=fake_encoder)
    engine.embed(["establish dimension"])
    with pytest.raises(SemanticError):
        engine.preload(SPORT, np.ones(DIM + 1))


def test_encoder_rejects_dimension_inconsistent_with_preloaded_vector():
    engine = SemanticEngine(encoder=fake_encoder)
    engine.preload(SPORT, np.ones(DIM + 1))
    with pytest.raises(SemanticError):
        engine.match(SPORT, topic())


def test_html_hidden_content_and_comments_do_not_influence_article():
    raw = '<script>политика</script><style>санкции</style><!-- переговоры --><p>Футбол&nbsp;и матч</p>'
    assert clean_text(raw) == "Футбол и матч"
    engine = SemanticEngine(encoder=fake_encoder)
    assert engine.match(art(1, raw), topic(description="политика санкции переговоры", threshold=0.1)) is None


def test_html_entities_are_decoded_once():
    assert clean_text("&lt;b&gt;текст&lt;/b&gt; &amp;amp;") == "<b>текст</b> &amp;"


def test_empty_cleaned_topic_never_invokes_encoder():
    encoder = CountingEncoder()
    engine = SemanticEngine(encoder=encoder)
    flt = topic(description="<p></p>", examples=["<br>", "<script>футбол</script>", "&nbsp;"])
    assert engine.topic_phrases(flt) == []
    assert engine.match(SPORT, flt) is None
    assert encoder.calls == []


def test_cleaned_topic_phrases_are_deduplicated_and_empty_examples_discarded():
    flt = topic(description="<b>футбол</b>", examples=["футбол", "<p></p>", "матч&nbsp;клуб"])
    assert SemanticEngine.topic_phrases(flt) == ["футбол", "матч клуб"]


@pytest.mark.parametrize("params", [
    ["not an object"],
    {"description": 123},
    {"description": "футбол", "examples": 123},
    {"description": "футбол", "examples": {"unexpected": "mapping"}},
    {"description": "футбол", "examples": ["матч", 123]},
])
def test_malformed_topic_params_raise_semantic_error(params):
    engine = SemanticEngine(encoder=fake_encoder)
    flt = Filter(id=1, user_id=1, name="invalid", kind="topic", params=params)
    with pytest.raises(SemanticError):
        engine.match(SPORT, flt)


def test_none_description_and_examples_are_an_empty_topic():
    encoder = CountingEncoder()
    engine = SemanticEngine(encoder=encoder)
    assert engine.match(SPORT, topic(description=None, examples=None)) is None
    assert encoder.calls == []


def test_disabled_cache_still_counts_each_computed_batch():
    encoder = CountingEncoder()
    engine = SemanticEngine(encoder=encoder, cache_size=0)
    articles = [art(1, "first"), art(2, "first"), art(3, "second")]
    assert engine.index(articles) == 2
    assert engine.index(articles) == 2
    assert encoder.texts_encoded == 4


@pytest.mark.parametrize("kwargs", [{"cache_size": -1}, {"max_chars": 0}, {"batch_size": 0}])
def test_invalid_engine_limits_are_a_semantic_error(kwargs):
    with pytest.raises(SemanticError):
        SemanticEngine(encoder=fake_encoder, **kwargs)


@pytest.mark.parametrize("params", [{"threshold": "invalid"}, {"aggregate": "median"}])
def test_invalid_filter_settings_fail_before_encoding(params):
    encoder = CountingEncoder()
    engine = SemanticEngine(encoder=encoder)
    with pytest.raises(SemanticError):
        engine.match(SPORT, topic(**params))
    assert encoder.calls == []


@pytest.mark.parametrize("method", ["rank", "match_many"])
@pytest.mark.parametrize("flt", [
    topic(description="<br>", examples=["<p></p>"]),
    Filter(id=1, user_id=1, name="keyword", kind="keyword", params={"any": ["футбол"]}),
])
def test_batch_methods_ignore_empty_or_non_topic_filters_without_encoding(method, flt):
    encoder = CountingEncoder()
    engine = SemanticEngine(encoder=encoder)
    actual = getattr(engine, method)([SPORT, art(2, "another")], flt)
    assert actual == ([] if method == "rank" else [None, None])
    assert encoder.calls == []
