"""Precision / recall / F1 по кортежам (law_id, article, point, subpoint).

Считается на множествах (case_id, кортеж): дубли внутри одного текста не
учитываются. Уровни кумулятивные: уровень «point» засчитан, если совпали
law_id, article и point. Уровень «subpoint» — это полное совпадение кортежа.
"""
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Set, Tuple

Link = Tuple[Optional[int], Optional[str], Optional[str], Optional[str]]

LEVELS: List[Tuple[str, int]] = [
    ("law_id", 1),
    ("article", 2),
    ("point", 3),
    ("subpoint", 4),
]


@dataclass
class Score:
    tp: int = 0
    fp: int = 0
    fn: int = 0

    @property
    def precision(self) -> float:
        return self.tp / (self.tp + self.fp) if self.tp + self.fp else 1.0

    @property
    def recall(self) -> float:
        return self.tp / (self.tp + self.fn) if self.tp + self.fn else 1.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if p + r else 0.0


def project(link: Link, depth: int) -> tuple:
    return tuple(link[:depth])


def score_level(expected: Dict[str, Iterable[Link]], predicted: Dict[str, Iterable[Link]],
                depth: int) -> Score:
    """expected/predicted: case_id -> ссылки. Кейс без предсказаний = пустой список."""
    s = Score()
    for case_id, exp in expected.items():
        e: Set[tuple] = {project(x, depth) for x in exp}
        p: Set[tuple] = {project(x, depth) for x in predicted.get(case_id, [])}
        s.tp += len(e & p)
        s.fp += len(p - e)
        s.fn += len(e - p)
    return s


def score_all(expected: Dict[str, Iterable[Link]],
              predicted: Dict[str, Iterable[Link]]) -> Dict[str, Score]:
    return {name: score_level(expected, predicted, depth) for name, depth in LEVELS}
