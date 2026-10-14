"""Сверка с чужой моделью на фрагментах открытого опроса fpakhurov/law-links-votes.

    python -m eval.survey [--local main:detect_links]

Скачивает с HuggingFace четыре файла `items/survey*.jsonl` (~2 МБ), берёт фрагменты типа verify
и сравнивает наш ответ со ссылками, которые на них вернула модель авторов. Это НЕ разметка людей:
ссылки модели авторов бывают неполными, а название закона часто лежит вне фрагмента.
Поэтому фрагменты с отсылками (настоящего/названного/указанного) исключаются.
"""
import argparse
import json
import re
import sys
import urllib.request
from collections import defaultdict
from importlib import import_module

from eval.metrics import LEVELS, score_all

BASE = "https://huggingface.co/datasets/fpakhurov/law-links-votes/resolve/main/items/survey{}.jsonl"
ANAPHORA = re.compile(r"настоящ|названн|указанн|вышеназв|данн", re.IGNORECASE)


def load_fragments():
    groups = defaultdict(set)
    for n in range(1, 5):
        with urllib.request.urlopen(BASE.format(n), timeout=60) as resp:
            for line in resp.read().decode("utf-8").splitlines():
                item = json.loads(line)
                if item["kind"] != "verify":
                    continue
                for x in item["links"]:
                    groups[item["fragment"]].add(
                        (x["law_id"], x["article"], x["point_article"], x["subpoint_article"]))
    return {frag: links for frag, links in groups.items() if not ANAPHORA.search(frag)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--local", default="main:detect_links")
    args = ap.parse_args()
    module, func = args.local.split(":")
    detect = getattr(import_module(module), func)

    fragments = load_fragments()
    expected = {str(i): list(links) for i, links in enumerate(fragments.values())}
    predicted = {}
    for i, frag in enumerate(fragments):
        predicted[str(i)] = [(x.law_id, x.article, x.point_article, x.subpoint_article)
                             for x in detect(frag)]
    scores = score_all(expected, predicted)
    print(f"{len(fragments)} фрагментов без отсылок; эталон — ответы модели авторов, не люди")
    print(f"{'уровень':<10}{'P':>7}{'R':>7}{'F1':>7}")
    for name, _ in LEVELS:
        s = scores[name]
        print(f"{name:<10}{s.precision:>7.3f}{s.recall:>7.3f}{s.f1:>7.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
