"""Эталоны из репозитория fpakhurov/law-links (MIT, см. eval/data/law-links/LICENSE).

Три набора: gold (синтетика и README), gold_real (реальные судебные тексты,
на нём их модель настраивалась), gold_test (отложенный набор).
Договорённость их разметки: ссылки на законы, которых нет в law_aliases.json,
в эталон не входят. Для сравнения запускайте eval с --drop-unknown.
"""
import json
from pathlib import Path
from typing import List

from eval.gold import Case

DATA = Path(__file__).resolve().parent / "data" / "law-links"
SETS = {"gold": "ext_gold", "gold_real": "ext_real", "gold_test": "ext_test"}


def load_external() -> List[Case]:
    cases: List[Case] = []
    for fname, tag in SETS.items():
        path = DATA / f"{fname}.json"
        if not path.exists():
            continue
        for c in json.loads(path.read_text(encoding="utf-8"))["cases"]:
            text = c["text"] if "text" in c else (DATA / c["text_file"]).read_text(encoding="utf-8")
            expected = [(l["law_id"], l["article"], l["point_article"], l["subpoint_article"])
                        for l in c["links"]]
            cases.append(Case(f"{tag}:{c['id']}", text, expected, [tag, "external"]))
    return cases
