from link_extractor import extract_links

def _one(text, **expected):
    res = extract_links(text)
    assert len(res) == 1, f"ожидали 1 ссылку в {text!r}, получили {res}"
    got = res[0]
    for k, v in expected.items():
        assert getattr(got, k) == v, (
            f"{text!r}: поле {k}: ожидали {v!r}, получили {getattr(got, k)!r}"
        )
    return got

def test_forward_order_full():
    _one(
        "пп. 1 п. 2 ст. 3 НК РФ",
        article="3",
        point="2",
        subpoint="1",
        law_text="НК РФ",
    )

def test_reverse_order_constitution():
    _one(
        "статья 17 Конституции",
        article="17",
        point=None,
        subpoint=None,
        law_text="Конституции",
    )

def test_in_point_of_article():
    _one(
        "в пункте 1 статьи 34",
        article="34",
        point="1",
    )

def test_link_without_law():
    _one(
        "часть 3, ст. 30.1",
        article="30.1",
        point="3",
        law_text=None,
    )

def test_typo_punta():
    _one(
        "пунта 1 ст. 5 УК РФ",
        article="5",
        point="1",
        law_text="УК РФ",
    )

def test_lists_are_not_split():
    _one(
        "пп. а, б п. 3, 4 ст. 5",
        article="5",
        point="3, 4",
        subpoint="а, б",
    )

def test_koap_and_noise_tail():
    _one(
        "ст. 12.15 КоАП РФ и далее",
        article="12.15",
        law_text="КоАП РФ",
    )

def test_multiple_links():
    text = "см. ст. 30.1 УК РФ и п. 2 ст. 3 НК РФ"
    res = extract_links(text)
    assert len(res) == 2
    assert res[0].article == "30.1"
    assert res[0].law_text == "УК РФ"
    assert res[1].article == "3"
    assert res[1].point == "2"
    assert res[1].law_text == "НК РФ"
    assert res[0].start < res[1].start

def test_dotted_and_range_values_are_kept():
    _one("ст. 43.2-6 УК РФ", article="43.2-6", law_text="УК РФ")

def test_bare_subpoint():
    _one("пп. 1.1 ст. 5", article="5", subpoint="1.1")

def test_empty_text():
    assert extract_links("") == []

def test_no_links():
    assert extract_links("просто текст без ссылок") == []

def test_law_quoted():
    _one(
        'ст. 5 Закона "О полиции"',
        article="5",
        law_text='Закона "О полиции"',
    )

def test_start_end_positions():
    text = "см. ст. 30.1 УК РФ конец"
    res = extract_links(text)
    assert len(res) == 1
    r = res[0]
    assert text[r.start:r.start + 3] == "ст."
    assert "УК РФ" in text[r.start:r.end]