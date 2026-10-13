# fin_tech_da_nlp

## Групповое задание №1: сервис выделения юридических ссылок

Условие задания — в [homework_1/README.md](homework_1/README.md).

FastAPI-сервис принимает текст и возвращает найденные в нём юридические ссылки
(`law_id`, `article`, `point_article`, `subpoint_article`).

### Запуск в Docker

```bash
cd homework_1

docker build -t law-links-service .
docker run -d -p 8978:8978 --name law-links-container law-links-service
```

При старте сервис строит индекс названий законов из `law_aliases.json`,
это занимает около 30 секунд. Готовность можно проверить так:

```bash
curl http://localhost:8978/health
```

Запрос:

```bash
curl -X POST "http://localhost:8978/detect" \
     -H "Content-Type: application/json" \
     -d '{"text": "В соответствии с пп. 1, 2, 3 п. 2 ст. 3 НК РФ"}'
```

Ответ:

```json
{
  "links": [
    {"law_id": 15, "article": "3", "point_article": "2", "subpoint_article": "1"},
    {"law_id": 15, "article": "3", "point_article": "2", "subpoint_article": "2"},
    {"law_id": 15, "article": "3", "point_article": "2", "subpoint_article": "3"}
  ]
}
```

Документация API: http://localhost:8978/docs

Остановка и удаление:

```bash
docker stop law-links-container
docker rm law-links-container
docker rmi law-links-service
```

### Запуск без Docker

```bash
cd homework_1
pip install -r requirements.txt
python main.py
```

### Как устроен сервис

Запрос проходит конвейер из трёх модулей, которые связывает `detect_links` в `main.py`:

```
текст → extract_links → RawLink → find_law + expand → LawLink → ответ
```

| Файл | Что делает |
|------|------------|
| `contracts.py` | общий тип `RawLink` — ссылка с сырыми значениями уровней |
| `link_extractor.py` | `extract_links(text)` — находит ссылки в тексте и режет их на уровни |
| `law_matcher.py` | `find_law(law_text)` — по названию закона находит `law_id` |
| `number_parser.py` | `expand(raw, law_id)` — разворачивает перечисления в отдельные ссылки |
| `main.py` | FastAPI-приложение и конвейер `detect_links` |
| `tests/`, `eval/` | юнит-тесты, эталонная разметка и метрики |

Модули друг друга не импортируют. Если закон не найден в базе, ссылка возвращается с `law_id: null`.

### Тесты и оценка качества

```bash
cd homework_1
pip install pytest

pytest                                        # юнит-тесты
python -m eval.run --local main:detect_links  # метрики без запуска сервиса
python -m eval.run                            # метрики по HTTP, сервис должен быть запущен
```

Подробнее про оценку — в [homework_1/eval/README.md](homework_1/eval/README.md).
