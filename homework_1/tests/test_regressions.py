"""Регрессии, найденные на внешних эталонах (eval/data/law-links)."""
import pytest

from link_extractor import extract_links
from law_matcher import find_law
from number_parser import expand


def fields(text):
    return [(r.article, r.point, r.subpoint, r.law_text) for r in extract_links(text)]


@pytest.mark.parametrize("text,expected", [
    ('п. «б» ч. 1 ст. 58 УК РФ', ("58", "1", "«б»", "УК РФ")),
    ('подпунктом "г" пункта 6 части 1 статьи 81 ТК РФ', ("81", "6", '"г"', "ТК РФ")),
    ("ст. 105 ч. 2 п. а УК РФ", ("105", "2", "а", "УК РФ")),
    ("ст. 20.4 ч. 1 КоАП РФ", ("20.4", "1", None, "КоАП РФ")),
    ("п. 2 пп. 2 ст. 54.1 НК РФ", ("54.1", "2", "2", "НК РФ")),
    ("подп. 3 п. 1 ст. 23 НК РФ", ("23", "1", "3", "НК РФ")),
    ("п. 6 ч. 1 ст. 24.5. КоАП РФ", ("24.5", "1", "6", "КоАП РФ")),
    ("по ст. 158 ук рф", ("158", None, None, "ук рф")),
    ("п. 3, 6 - 8 ст. 171 НК РФ", ("171", "3, 6 - 8", None, "НК РФ")),
])
def test_chain_shapes(text, expected):
    (got,) = fields(text)
    assert got == expected


def test_sentence_boundary_is_not_law():
    (r,) = extract_links("Это ст. 5. НК РФ не применяется.")
    assert r.law_text is None


def test_two_links_with_comma_not_merged():
    assert [f[:3] for f in fields("ст. 12.8 ч. 1, ст. 12.26 ч. 2 КоАП РФ")] == [
        ("12.8", "1", None), ("12.26", "2", None)]


def test_double_st():
    (r,) = extract_links("ст.ст. 64 и 73 УК РФ")
    assert (r.article, r.law_text) == ("64 и 73", "УК РФ")


def test_pp_without_point_means_points():
    (r,) = extract_links("п.п. 3, 4 ст. 487 ГК РФ")
    assert (r.point, r.subpoint) == ("3, 4", None)


def test_law_with_date_and_number():
    (r,) = extract_links('ст. 11 Федерального закона от 01.04.1996 №27-ФЗ «Об учёте»')
    assert "27-ФЗ" in r.law_text and "Об учёте" in r.law_text


@pytest.mark.parametrize("value,expected", [
    ("«и», «к»", ["и", "к"]),
    ("первой", ["1"]),
    ("6 - 8", ["6-8"]),
])
def test_number_parser_forms(value, expected):
    from contracts import RawLink
    got = [l.point_article for l in expand(RawLink(0, 1, "5", value, None), 15)]
    assert got == expected


@pytest.mark.parametrize("name,law_id", [
    ("Закона № 580-ФЗ", 81),
    ("Закона N 27-ФЗ", 498),
    ("Федерального закона от 29 декабря 2022 года № 580-ФЗ", 81),
    ("Кодекса административного судопроизводства Российской Федерации", 18),
])
def test_find_law_by_number_and_title(name, law_id):
    assert find_law(name) == law_id


def test_find_law_unknown_number():
    assert find_law("Закона № 99999-ФЗ") is None


@pytest.mark.parametrize("text,law_id", [
    ("п. 2 ст. 5 Указа Президента Российской Федерации №240", None),
    ("ст. 5 Уголовно-исполнительного кодекса Российской Федерации", 11),
    ("ст. 5 Кодекса внутреннего водного транспорта России", 19),
    ("ст. 7 ПБУ 9/99", None),
])
def test_law_text_covers_non_code_acts(text, law_id):
    (r,) = extract_links(text)
    assert r.law_text and len(r.law_text.split()) >= 2 or "ПБУ" in r.law_text
    if law_id is not None:
        assert find_law(r.law_text) == law_id


def test_numbers_inside_law_title_are_not_links():
    links = extract_links("ст. 332 ФЗ «О введении в действие части четвертой Гражданского кодекса Российской Федерации»")
    assert len(links) == 1 and links[0].article == "332"
