"""Замер скорости сервиса против лимита проверки: 4 бенча по 500 текстов за 5 минут.

    python -m scripts.speed                    # HTTP, http://localhost:8978/detect
    python -m scripts.speed --local            # прямой вызов detect_links
    python -m scripts.speed --texts 2000 --limit 300

Тексты собираются из эталонов (README, внешние реальные тексты, синтетика), поэтому
длина и плотность ссылок близки к реальным. Код возврата 1, если лимит превышен.
"""
import argparse
import json
import random
import sys
import time
import urllib.request

from eval.external import load_external
from eval.gold import CASES
from eval.synthetic import make_cases

DEFAULT_URL = "http://localhost:8978/detect"


def build_texts(n: int, seed: int = 1) -> list[str]:
    pool = [c.text for c in CASES] + [c.text for c in load_external()] + [c.text for c in make_cases(300)]
    rnd = random.Random(seed)
    return [rnd.choice(pool) for _ in range(n)]


def http_call(url: str):
    def call(text: str):
        req = urllib.request.Request(url, data=json.dumps({"text": text}).encode("utf-8"),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read())["links"]
    return call


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--url", default=DEFAULT_URL)
    ap.add_argument("--local", action="store_true")
    ap.add_argument("--texts", type=int, default=2000)
    ap.add_argument("--limit", type=float, default=300.0, help="лимит в секундах")
    args = ap.parse_args()

    if args.local:
        import main as service
        service_call = service.detect_links
    else:
        service_call = http_call(args.url)

    texts = build_texts(args.texts)
    avg_len = sum(map(len, texts)) / len(texts)
    start = time.perf_counter()
    links = 0
    for t in texts:
        links += len(service_call(t))
    total = time.perf_counter() - start
    print(f"{len(texts)} текстов (средняя длина {avg_len:.0f} символов), {links} ссылок")
    print(f"всего {total:.1f} с, {total / len(texts) * 1000:.1f} мс на текст, лимит {args.limit:.0f} с "
          f"(запас в {args.limit / total:.0f} раз)")
    return 0 if total <= args.limit else 1


if __name__ == "__main__":
    sys.exit(main())
