"""Сравнение моделей и подбор порога на размеченном мини-наборе (data/semantic_dev.json).

    python -m scripts.bench_semantic                       # модель по умолчанию
    python -m scripts.bench_semantic --models cointegrated/rubert-tiny2 intfloat/multilingual-e5-small

Для каждой темы считается ROC-AUC (насколько оценка вообще разделяет подходящие новости и остальные,
не зависит от порога), лучший F1 и порог, на котором он достигается. Общий порог по умолчанию —
тот, что максимизирует средний F1 по темам. Набор размечен вручную и мал, поэтому порог — ориентир, а не истина.
"""
import argparse
import json
import time
from pathlib import Path

import numpy as np

from app.contracts import Article, Filter
from app.engines.semantic import SemanticEngine

DATA = Path(__file__).resolve().parents[1] / "data" / "semantic_dev.json"
E5 = {"query_prefix": "query: ", "passage_prefix": "passage: "}


def load():
    data = json.loads(DATA.read_text(encoding="utf-8"))
    articles = [Article(id=i["id"], feed_id=0, guid=str(i["id"]), title=i["title"], summary=i["summary"], link="")
                for i in data["items"]]
    return data, articles


def roc_auc(scores: np.ndarray, labels: np.ndarray) -> float:
    """Вероятность, что случайная подходящая новость оценена выше случайной неподходящей."""
    pos, neg = scores[labels], scores[~labels]
    if len(pos) == 0 or len(neg) == 0:
        return float("nan")
    greater = (pos[:, None] > neg[None, :]).sum() + 0.5 * (pos[:, None] == neg[None, :]).sum()
    return float(greater / (len(pos) * len(neg)))


def best_f1(scores: np.ndarray, labels: np.ndarray):
    best = (0.0, 0.0)
    for t in np.unique(scores):
        pred = scores >= t
        tp = int((pred & labels).sum())
        fp, fn = int((pred & ~labels).sum()), int((~pred & labels).sum())
        f1 = 2 * tp / (2 * tp + fp + fn) if tp else 0.0
        if f1 > best[0]:
            best = (f1, float(t))
    return best


def f1_at(scores: np.ndarray, labels: np.ndarray, t: float) -> float:
    pred = scores >= t
    tp = int((pred & labels).sum())
    fp, fn = int((pred & ~labels).sum()), int((~pred & labels).sum())
    return 2 * tp / (2 * tp + fp + fn) if tp else 0.0


def evaluate(model: str, min_positives: int = 7) -> dict:
    data, articles = load()
    kwargs = E5 if "e5" in model else {}
    engine = SemanticEngine(model, **kwargs)
    t0 = time.perf_counter()
    engine.embed(["прогрев"])
    load_s = time.perf_counter() - t0
    t0 = time.perf_counter()
    engine.index(articles)
    index_s = time.perf_counter() - t0

    per_topic = {}
    for name, topic in data["topics"].items():
        flt = Filter(id=0, user_id=0, name=name, kind="topic", params={"description": topic["description"]})
        scores = np.array([engine.score(a, flt)[0] for a in articles])
        labels = np.array([name in i["topics"] for i in data["items"]])
        if labels.sum() < min_positives:
            continue  # слишком мало примеров, оценка шумная
        f1, thr = best_f1(scores, labels)
        per_topic[name] = {"auc": roc_auc(scores, labels), "f1": f1, "thr": thr, "scores": scores, "labels": labels,
                           "n_pos": int(labels.sum())}

    grid = np.linspace(0.0, 1.0, 201)
    mean_f1 = [np.mean([f1_at(t["scores"], t["labels"], g) for t in per_topic.values()]) for g in grid]
    default_thr = float(grid[int(np.argmax(mean_f1))])
    return {"model": model, "per_topic": per_topic, "default_thr": default_thr, "mean_f1": float(max(mean_f1)),
            "mean_auc": float(np.mean([t["auc"] for t in per_topic.values()])),
            "load_s": load_s, "index_s": index_s, "n": len(articles)}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--models", nargs="+", default=["cointegrated/rubert-tiny2"])
    args = ap.parse_args()
    for model in args.models:
        r = evaluate(model)
        print(f"\n=== {r['model']} ===  загрузка {r['load_s']:.1f} с, индексация {r['n']} статей {r['index_s']:.2f} с "
              f"({r['n'] / r['index_s']:.0f} статей/с)")
        print(f"{'тема':<11}{'AUC':>7}{'лучший F1':>11}{'порог':>8}{'примеров':>10}")
        for name, t in r["per_topic"].items():
            print(f"{name:<11}{t['auc']:>7.3f}{t['f1']:>11.3f}{t['thr']:>8.3f}{t['n_pos']:>10}")
        print(f"среднее: AUC {r['mean_auc']:.3f}; общий порог {r['default_thr']:.2f} даёт средний F1 {r['mean_f1']:.3f}")
        curve = "  ".join(f"{g:.2f}: {np.mean([f1_at(t['scores'], t['labels'], g) for t in r['per_topic'].values()]):.3f}"
                          for g in (0.36, 0.40, 0.44, 0.48, 0.52, 0.56, 0.60))
        print(f"средний F1 по порогам — {curve}")


if __name__ == "__main__":
    main()
