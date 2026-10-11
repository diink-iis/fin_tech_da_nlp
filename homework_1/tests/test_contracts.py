"""Тесты контрактов между модулями (см. «NLP ДЗ №1: разбиение на 4 человек и контракты»).

Каждый блок пропускается, пока соответствующий модуль не появился в репозитории.
Модули друг друга не импортируют, поэтому и тесты проверяют их независимо.
"""
import pytest

from eval.gold import CASES

contracts = pytest.importorskip("contracts")
RawLink = contracts.RawLink


def as_tuple(link):
    return (link.law_id, link.article, link.point_article, link.subpoint_article)


# ---------------------------------------------------------------- contracts.py
def test_rawlink_defaults():
    r = RawLink(start=0, end=5)
    assert (r.article, r.point, r.subpoint, r.law_text) == (None, None, None, None)


# --------------------------------------------------------------- number_parser
class TestExpand:
    @pytest.fixture(autouse=True)
    def _expand(self):
        self.expand = pytest.importorskip("number_parser").expand

    def run(self, article=None, point=None, subpoint=None, law_id=15):
        return [as_tuple(x) for x in self.expand(RawLink(0, 1, article, point, subpoint), law_id)]

    @pytest.mark.parametrize("article,point,subpoint,expected", [
        ("12", None, None, [(15, "12", None, None)]),
        ("374", "1", "1", [(15, "374", "1", "1")]),
        ("3", "2", "1, 2, 3", [(15, "3", "2", s) for s in "123"]),
        ("14", "1", "4, 5, 6 и 8", [(15, "14", "1", s) for s in "4568"]),
        ("10", "2", "5 и 6", [(15, "10", "2", "5"), (15, "10", "2", "6")]),
        ("5", "2", "1, 1", [(15, "5", "2", "1")]),                       # дубли
        ("43.2-6", None, None, [(15, "43.2-6", None, None)]),            # диапазон не режем
        ("12", "4.6", None, [(15, "12", "4.6", None)]),                  # точка не разделитель
        ("66", "3.345, 23", "а, б и с",
         [(15, "66", p, s) for p in ("3.345", "23") for s in "абс"]),
        (None, "10", None, [(15, None, "10", None)]),
    ])
    def test_table(self, article, point, subpoint, expected):
        assert sorted(self.run(article, point, subpoint), key=repr) == sorted(expected, key=repr)

    def test_order_preserved(self):
        assert [t[3] for t in self.run("3", "2", "3, 1, 2")] == ["3", "1", "2"]

    def test_cartesian_size(self):
        assert len(self.run("5", "3, 4", "а, б")) == 4

    def test_none_stays_none(self):
        assert self.run(None, None, None) in ([], [(15, None, None, None)])
        for t in self.run("5", None, None):
            assert t[2] is None and t[3] is None

    def test_law_id_none(self):
        assert self.run("5", law_id=None) == [(None, "5", None, None)]

    @pytest.mark.parametrize("garbage", ["", "   ", ",", "и", "---", "§§", "1,,2"])
    def test_garbage_does_not_raise(self, garbage):
        result = self.expand(RawLink(0, 1, "5", garbage, garbage), 15)
        assert isinstance(result, list)


# ---------------------------------------------------------------- law_matcher
class TestFindLaw:
    @pytest.fixture(autouse=True)
    def _find(self):
        self.find_law = pytest.importorskip("law_matcher").find_law

    @pytest.mark.parametrize("name,law_id", [
        ("НК РФ", 15), ("УК РФ", 13), ("АПК", 0), ("КоАП РФ", 17),
        ("Гражданского кодекса Российской Федерации", 5),
        ("Гражданского Кодекса", 5),
        ("Налогового Кодекса Российской Федерации", 15),
        ("Семейного кодекса Российской Федерации", 8),
        ("Трудового кодекса Российской Федерации", 10),
        ("Гражданского процессуального кодекса Российской Федерации", 6),
        ('Закона "О защите прав потребителей"', 984),
        ('Закона "О персональных данных"', 330),
        ('Федерального закона "О бухгалтерском учёте"', 224),
        ('Федерального закона "О полиции"', 249),
        ('Федерального закона "О защите конкуренции"', 332),
        ('Федерального закона "О государственной регистрации недвижимости"', 174),
        ('Федерального закона "О государственной гражданской службе Российской Федерации"', 362),
        ('Федерального закона "О государственной регистрации юридических лиц и индивидуальных предпринимателей"', 412),
        ("Федерального закона «О полиции»", 249),
    ])
    def test_known(self, name, law_id):
        assert self.find_law(name) == law_id

    @pytest.mark.parametrize("name", [
        "Конституции Российской Федерации", "рандомного кодекса", "ФЗ №20-456",
        'Федерального закона "О банках и банковской деятельности"',
        "", "   ", "кодекс", "закон",
    ])
    def test_unknown_gives_none(self, name):
        assert self.find_law(name) is None

    def test_none_input(self):
        assert self.find_law(None) is None


# -------------------------------------------------------------- link_extractor
class TestExtractLinks:
    @pytest.fixture(autouse=True)
    def _extract(self):
        self.extract_links = pytest.importorskip("link_extractor").extract_links

    @pytest.mark.parametrize("text", ["", "   ", "\n\n", "Просто текст без ссылок.", "ст.", "п. п. п."])
    def test_no_links_no_raise(self, text):
        assert self.extract_links(text) == []

    def test_positions_valid_and_ordered(self):
        text = next(c.text for c in CASES if c.id == "readme_full")
        links = self.extract_links(text)
        assert links, "в тексте README ссылки есть"
        prev_start = -1
        for r in links:
            assert 0 <= r.start < r.end <= len(text)
            assert r.start >= prev_start, "порядок появления в тексте"
            prev_start = r.start

    def test_basic_split(self):
        text = "В соответствии с пп. 1 п. 1 ст. 374 НК РФ объекты"
        (r,) = self.extract_links(text)
        assert (r.article, r.point, r.subpoint) == ("374", "1", "1")
        assert r.law_text == "НК РФ"
        assert text[r.start:r.end].startswith("пп.") and "НК РФ" in text[r.start:r.end]

    def test_enumeration_not_expanded(self):
        (r,) = self.extract_links("пп. 1, 2, 3 п. 2 ст. 3 НК РФ")
        assert r.subpoint == "1, 2, 3"

    def test_reverse_order(self):
        (r,) = self.extract_links("в пункте 1 статьи 34 Гражданского кодекса РФ")
        assert (r.article, r.point) == ("34", "1")

    def test_no_law(self):
        (r,) = self.extract_links("часть 3, ст. 30.1 (постановления обжалуются)")
        assert (r.article, r.point, r.law_text) == ("30.1", "3", None)

    def test_law_text_does_not_grab_neighbours(self):
        (r,) = self.extract_links("Суд применил ст. 5 УК РФ в отношении подсудимого")
        assert r.law_text == "УК РФ"

    def test_quoted_law(self):
        (r,) = self.extract_links('согласно ст. 5 Федерального закона "О полиции", полицейские')
        assert "О полиции" in r.law_text and "полицейские" not in r.law_text

    def test_levels_none_not_empty_string(self):
        (r,) = self.extract_links("ст. 12 НК РФ")
        assert r.point is None and r.subpoint is None

    def test_does_not_mutate_or_raise_on_huge_input(self):
        assert isinstance(self.extract_links("ст. 5 НК РФ. " * 5000), list)


# --------------------------------------------------------------- detect_links
class TestDetectLinks:
    @pytest.fixture(autouse=True)
    def _detect(self):
        pytest.importorskip("fastapi")
        main = pytest.importorskip("main")
        self.detect_links = getattr(main, "detect_links", None)
        if self.detect_links is None:
            pytest.skip("detect_links ещё не реализован в main.py")

    def test_empty(self):
        assert list(self.detect_links("")) == []

    def test_readme_example(self):
        got = {as_tuple(x) for x in self.detect_links("пп. 1, 2, 3 п. 2 ст. 3 НК РФ")}
        assert got == {(15, "3", "2", s) for s in "123"}

    def test_unknown_law_gives_none_law_id(self):
        got = {as_tuple(x) for x in self.detect_links("ст. 37 ФЗ №20-456")}
        assert got == {(None, "37", None, None)}

    def test_repeatable(self):
        text = "ст. 5 НК РФ и ст. 7 УК РФ"
        assert list(map(as_tuple, self.detect_links(text))) == list(map(as_tuple, self.detect_links(text)))
