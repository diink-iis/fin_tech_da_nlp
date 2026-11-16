"""Загрузить и проверить модель до демонстрации: python -m scripts.prepare_semantic.

Повторный запуск с --offline проверяет работу из локального кэша без сети.
Каталог постоянного кэша задаётся стандартной переменной Hugging Face HF_HOME.
"""
from __future__ import annotations

import argparse
import os
import sys
from typing import Sequence


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", help="имя модели или локальный путь; по умолчанию cointegrated/rubert-tiny2")
    parser.add_argument("--offline", action="store_true", help="проверять только локальные файлы, без сети")
    args = parser.parse_args(argv)

    # Hugging Face reads this flag at import time, before the first model load.
    if args.offline:
        os.environ["HF_HUB_OFFLINE"] = "1"
    from app.engines.semantic import DEFAULT_MODEL, SemanticEngine, SemanticError

    model_name = args.model or DEFAULT_MODEL
    try:
        engine = SemanticEngine(model_name)
        vectors = engine.embed(["Проверка локальной модели новостей"])
    except SemanticError as exc:
        print(f"Не удалось подготовить модель {model_name}: {exc}", file=sys.stderr)
        return 1

    print(f"Модель готова: {model_name}; размерность вектора: {vectors.shape[1]}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
