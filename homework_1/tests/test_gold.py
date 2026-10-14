"""Проверка самого эталона: ошибка в разметке портит оценку всей команды."""
import json
from pathlib import Path

import pytest

from eval.gold import CASES

ALIASES = json.loads((Path(__file__).resolve().parents[1] / "law_aliases.json").read_text())
KNOWN = {int(k) for k in ALIASES}


def test_case_ids_unique():
    ids = [c.id for c in CASES]
    assert len(ids) == len(set(ids))


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.id)
def test_case_wellformed(case):
    assert len(case.expected) == len(set(case.expected)), "дубли в expected"
    for law_id, article, point, subpoint in case.expected:
        assert law_id is None or law_id in KNOWN
        for v in (article, point, subpoint):
            assert v is None or (isinstance(v, str) and v == v.strip() and v != "")


def test_readme_has_about_thirty_links():
    readme = next(c for c in CASES if c.id == "readme_full")
    assert 30 <= len(readme.expected) <= 45


def test_readme_text_matches_task():
    """Текст эталона должен совпадать с test_text из TASK.md."""
    readme_md = (Path(__file__).resolve().parents[1] / "TASK.md").read_text()
    start = readme_md.index('test_text = """') + len('test_text = """')
    task_text = readme_md[start:readme_md.index('"""', start)]
    gold_text = next(c for c in CASES if c.id == "readme_full").text

    def norm(t):  # хвостовые пробелы в строках не значимы
        return "\n".join(line.rstrip() for line in t.splitlines())

    assert norm(gold_text) == norm(task_text)
