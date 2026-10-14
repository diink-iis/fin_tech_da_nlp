"""Оценка сервиса одной командой.

    python -m eval.run                          # HTTP: http://localhost:8978/detect
    python -m eval.run --url http://host:8978/detect
    python -m eval.run --local main:detect_links   # вызвать функцию напрямую
    python -m eval.run --tag перечисление --min-f1 0.9
    python -m eval.run --only-external                 # внешние эталоны
    python -m eval.run --synthetic 500                 # плюс синтетика из базы законов
    python -m eval.run --no-drop-unknown               # учитывать ссылки с law_id=None

Запускать из корня homework_1. Код возврата 1, если F1 по полному кортежу ниже --min-f1.
"""
import argparse
import json
import sys
import urllib.request
from collections import defaultdict
from importlib import import_module
from typing import Callable, Dict, List

from eval.errors import collect_errors, format_errors
from eval.external import load_external
from eval.gold import CASES, Case, Link
from eval.holdout import HOLDOUT
from eval.synthetic import make_cases
from eval.metrics import LEVELS, score_all

DEFAULT_URL = "http://localhost:8978/detect"


def _to_tuple(item) -> Link:
    get = item.get if isinstance(item, dict) else lambda k: getattr(item, k, None)
    return (get("law_id"), get("article"), get("point_article"), get("subpoint_article"))


def http_detector(url: str, timeout: float = 30.0) -> Callable[[str], List[Link]]:
    def detect(text: str) -> List[Link]:
        req = urllib.request.Request(
            url, data=json.dumps({"text": text}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        return [_to_tuple(x) for x in body["links"]]
    return detect


def local_detector(target: str) -> Callable[[str], List[Link]]:
    module, func = target.split(":")
    fn = getattr(import_module(module), func)
    return lambda text: [_to_tuple(x) for x in fn(text)]


def predict(cases: List[Case], detect: Callable[[str], List[Link]]) -> Dict[str, List[Link]]:
    predicted: Dict[str, List[Link]] = {}
    for c in cases:
        try:
            predicted[c.id] = detect(c.text)
        except Exception as exc:  # упавший кейс считаем пустым ответом и показываем причину
            print(f"! [{c.id}] ошибка вызова: {exc!r}", file=sys.stderr)
            predicted[c.id] = []
    return predicted


def metrics_table(title: str, cases: List[Case], predicted: Dict[str, List[Link]]) -> str:
    scores = score_all({c.id: c.expected for c in cases}, predicted)
    rows = [f"{title}", f"{'уровень':<10}{'P':>7}{'R':>7}{'F1':>7}{'TP':>6}{'FP':>6}{'FN':>6}"]
    for name, _ in LEVELS:
        s = scores[name]
        rows.append(f"{name:<10}{s.precision:>7.3f}{s.recall:>7.3f}{s.f1:>7.3f}"
                    f"{s.tp:>6}{s.fp:>6}{s.fn:>6}")
    return "\n".join(rows)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--url", default=DEFAULT_URL)
    ap.add_argument("--local", metavar="module:function", help="вызвать функцию напрямую, без HTTP")
    ap.add_argument("--tag", action="append", help="только кейсы с этим тегом (можно несколько)")
    ap.add_argument("--external", action="store_true",
                    help="добавить эталоны fpakhurov/law-links (теги ext_gold, ext_real, ext_test)")
    ap.add_argument("--only-external", action="store_true", help="только внешние эталоны")
    ap.add_argument("--holdout", action="store_true", help="добавить отложенный набор (тег holdout)")
    ap.add_argument("--synthetic", type=int, metavar="N", default=0,
                    help="добавить N синтетических кейсов из law_aliases.json (тег synthetic)")
    ap.add_argument("--drop-unknown", action=argparse.BooleanOptionalAction, default=True,
                    help="не учитывать ссылки с law_id=None, и в эталоне, и в ответе (по умолчанию да; "
                         "соответствует сервису, который такие ссылки не возвращает)")
    ap.add_argument("--min-f1", type=float, default=0.0, help="порог F1 для кода возврата")
    ap.add_argument("--no-errors", action="store_true", help="не печатать список ошибок")
    args = ap.parse_args(argv)

    cases = ([] if args.only_external else list(CASES)) + (
        load_external() if args.external or args.only_external else [])
    if args.holdout:
        cases += HOLDOUT
    if args.synthetic:
        cases += make_cases(args.synthetic)
    cases = [c for c in cases if not args.tag or set(args.tag) & set(c.tags)]
    if args.drop_unknown:
        cases = [Case(c.id, c.text, [l for l in c.expected if l[0] is not None], c.tags)
                 for c in cases]
    detect = local_detector(args.local) if args.local else http_detector(args.url)
    predicted = predict(cases, detect)
    if args.drop_unknown:
        predicted = {k: [l for l in v if l[0] is not None] for k, v in predicted.items()}

    print(metrics_table(f"=== ИТОГО ({len(cases)} кейсов) ===", cases, predicted))

    by_tag: Dict[str, List[Case]] = defaultdict(list)
    for c in cases:
        for t in c.tags:
            by_tag[t].append(c)
    print("\n=== По тегам (полный кортеж) ===")
    print(f"{'тег':<22}{'кейсов':>7}{'P':>7}{'R':>7}{'F1':>7}")
    for tag, cs in sorted(by_tag.items()):
        s = score_all({c.id: c.expected for c in cs}, predicted)["subpoint"]
        print(f"{tag:<22}{len(cs):>7}{s.precision:>7.3f}{s.recall:>7.3f}{s.f1:>7.3f}")

    if not args.no_errors:
        errors = collect_errors({c.id: c.text for c in cases}, {c.id: c.expected for c in cases},
                                predicted)
        print(f"\n=== Ошибки: {len(errors)} из {len(cases)} кейсов ===\n")
        print(format_errors(errors))

    total = score_all({c.id: c.expected for c in cases}, predicted)["subpoint"]
    return 0 if total.f1 >= args.min_f1 else 1


if __name__ == "__main__":
    sys.exit(main())
