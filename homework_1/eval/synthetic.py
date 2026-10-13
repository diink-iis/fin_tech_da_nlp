"""Синтетический бенч из law_aliases.json: ссылка собирается из случайного закона и уровней.

Приближает скрытые бенчи (тексты, собранные из базы законов). Берутся только
однозначные алиасы, поэтому эталон всегда однозначен. Названия подставляются
в той форме, как записаны в базе, без склонений: это нижняя оценка сложности
по падежам, но проверяет, что любой тип акта (указ, распоряжение, закон,
стандарт) вообще вырезается из текста и находится в базе.
"""
import json
import random
from collections import Counter
from pathlib import Path
from typing import List

from eval.gold import Case

ALIASES = Path(__file__).resolve().parents[1] / "law_aliases.json"
ART_WORDS = ["ст. ", "статьи ", "статье ", "статья "]
TEMPLATES = ["В соответствии с {ref} суд решил.", "Согласно {ref}, заявление удовлетворить.",
             "Как указано в {ref}, срок не пропущен."]


def _laws():
    data = json.loads(ALIASES.read_text(encoding="utf-8"))
    count = Counter(a for v in data.values() for a in v)
    laws = [(int(i), [a for a in v if count[a] == 1 and len(a) < 120]) for i, v in data.items()]
    return [(i, v) for i, v in laws if v]


_GENITIVE = [("Федеральный закон", "Федерального закона"), ("Федеральный конституционный закон", "Федерального конституционного закона"),
             ("Указ ", "Указа "), ("Закон ", "Закона "), ("Распоряжение ", "Распоряжения ")]


def _genitive(name: str) -> str:
    """Родительный падеж для заголовка акта: «статьи 5 Указа Президента …»."""
    for src, dst in _GENITIVE:
        if name.startswith(src):
            return dst + name[len(src):]
    words = name.split(" ")
    if len(words) >= 2 and words[1].lower().startswith("кодекс") and words[0].endswith(("ый", "ий")):
        return words[0][:-2] + "ого " + words[1] + "а" + (" " + " ".join(words[2:]) if words[2:] else "")
    return name


def make_cases(n: int = 500, seed: int = 7) -> List[Case]:
    rnd = random.Random(seed)
    laws = _laws()
    cases = []
    for k in range(n):
        law_id, names = rnd.choice(laws)
        name = rnd.choice(names)
        if rnd.random() < 0.5:
            name = _genitive(name)
        article = str(rnd.randint(1, 400))
        point = rnd.choice(["1", "2", "3", "4.6", "12", "43.2"]) if rnd.random() < .7 else None
        subpoint = rnd.choice("абвг123") if point and rnd.random() < .5 else None
        parts = []
        if subpoint:
            parts.append(("пп. " if subpoint.isdigit() else "подпункт ") + subpoint)
        if point:
            parts.append("п. " + point)
        parts.append(rnd.choice(ART_WORDS) + article)
        text = rnd.choice(TEMPLATES).format(ref=" ".join(parts) + " " + name)
        cases.append(Case(f"synthetic:{k}", text, [(law_id, article, point, subpoint)],
                          ["synthetic"]))
    return cases
