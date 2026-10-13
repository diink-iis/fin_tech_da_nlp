"""Run: python -m pytest test_law_matcher.py -q"""
import pytest
from law_matcher import find_law, initialize


@pytest.fixture(scope='module', autouse=True)
def index():
    initialize()


@pytest.mark.parametrize('text, expected', [
    ('УК РФ', 13),
    ('уголовного кодекса', 13),
    ('НК РФ', 15),
    ('Гражданского кодекса Российской Федерации', 5),
    ('АПК', 0),
    ('Закона "О полиции"', 249),
    ('Федерального закона "О персональных данных"', 330),
    ('Федерального закона "О государственной регистрации недвижимости"', 174),
    ('ФЗ №20-456', None),
    ('рандомного кодекса', None),
    (None, None),
    ('', None),
    ('Конституции Российской Федерации', None),  # absent from supplied JSON
])
def test_match(text, expected):
    assert find_law(text) == expected
