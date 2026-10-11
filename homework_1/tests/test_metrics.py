import pytest

from eval.errors import EXTRACTOR, EXTRACTOR_OR_PARSER, LAW_MATCHER, diff_case
from eval.metrics import score_all, score_level

A = (15, "374", "1", "1")


def test_perfect_match():
    s = score_all({"c": [A]}, {"c": [A]})
    assert all(x.f1 == 1.0 for x in s.values())


def test_missing_and_extra_counts():
    b = (13, "105", None, None)
    s = score_level({"c": [A, b]}, {"c": [A, (13, "999", None, None)]}, 4)
    assert (s.tp, s.fp, s.fn) == (1, 1, 1)
    assert s.precision == s.recall == 0.5


def test_levels_are_cumulative():
    # закон и статья верны, пункт нет
    exp, pred = {"c": [(15, "374", "1", None)]}, {"c": [(15, "374", "2", None)]}
    s = score_all(exp, pred)
    assert s["law_id"].f1 == 1.0
    assert s["article"].f1 == 1.0
    assert s["point"].f1 == 0.0
    assert s["subpoint"].f1 == 0.0


def test_wrong_law_breaks_all_levels():
    s = score_all({"c": [(15, "5", None, None)]}, {"c": [(13, "5", None, None)]})
    assert all(x.f1 == 0.0 for x in s.values())


def test_duplicates_collapse():
    s = score_level({"c": [A]}, {"c": [A, A]}, 4)
    assert (s.tp, s.fp, s.fn) == (1, 0, 0)


def test_same_link_in_different_cases_counts_separately():
    s = score_level({"x": [A], "y": [A]}, {"x": [A]}, 4)
    assert (s.tp, s.fn) == (1, 1)


def test_none_expected_none_predicted_is_perfect():
    s = score_level({"c": []}, {"c": []}, 4)
    assert s.precision == s.recall == s.f1 == 1.0
    assert (s.tp, s.fp, s.fn) == (0, 0, 0)


def test_false_positive_on_negative_case():
    s = score_level({"c": []}, {"c": [A]}, 4)
    assert (s.tp, s.fp, s.fn) == (0, 1, 0)
    assert s.precision == 0.0


def test_missing_prediction_for_case_is_empty():
    s = score_level({"c": [A]}, {}, 4)
    assert (s.tp, s.fp, s.fn) == (0, 0, 1)


def test_diff_case_none_when_equal():
    assert diff_case("c", "t", [A], [A]) is None


def test_hint_law_matcher():
    err = diff_case("c", "t", [(15, "5", None, None)], [(13, "5", None, None)])
    assert any(LAW_MATCHER in h for h in err.hints)


def test_hint_levels():
    err = diff_case("c", "t", [(15, "5", "1", "2")], [(15, "5", "1", None)])
    assert any(EXTRACTOR_OR_PARSER in h for h in err.hints)


def test_hint_not_found():
    err = diff_case("c", "t", [(15, "5", None, None)], [])
    assert any(EXTRACTOR in h and "не найдена" in h for h in err.hints)
