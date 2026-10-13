# Оценка качества (участник 5)

Запускать из корня `homework_1`.

```bash
python -m eval.run                       # по HTTP: http://localhost:8978/detect
python -m eval.run --local main:detect_links   # вызвать функцию напрямую
python -m eval.run --tag перечисление    # только кейсы с тегом
python -m eval.run --min-f1 0.9          # код возврата 1, если F1 ниже порога
python -m eval.run --only-external --drop-unknown   # внешние эталоны (fpakhurov/law-links, MIT)
pytest                                   # юнит-тесты (тесты модулей пропускаются, пока модуля нет)
```

- `gold.py` — эталон: текст → ожидаемые `(law_id, article, point, subpoint)`. Закон не из базы = `law_id None`.
- `metrics.py` — P/R/F1 по уровням; уровни кумулятивные (`point` верен, если верны law_id, article и point).
- `errors.py` — показ ошибок: ожидали / получили / в каком модуле искать причину (эвристика).
- Новый кейс добавляется в `CASES` в `gold.py`; `tests/test_gold.py` проверит, что law_id существуют в базе.
- `external.py` + `data/law-links/` — эталоны чужого репозитория [fpakhurov/law-links](https://github.com/fpakhurov/law-links) (MIT, лицензия рядом). Их разметка не содержит ссылок на законы вне `law_aliases.json`, поэтому сравнивать нужно с `--drop-unknown`.
- `gold_test` у них отложенный набор; мы смотрели на его ошибки при доработке, так что для честной оценки нужны новые тексты.
