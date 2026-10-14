# fin_tech_da_nlp

Репозиторий с домашними заданиями по NLP для финтеха.

| Задание | Что | Описание |
|---------|-----|----------|
| №1 | FastAPI-сервис, выделяющий юридические ссылки из текста (`ст. 5 НК РФ` → `law_id`, статья, пункт, подпункт) | [homework_1/README.md](homework_1/README.md), условие — [homework_1/TASK.md](homework_1/TASK.md) |

Запуск первого задания:

```bash
cd homework_1
docker build -t law-links-service .
docker run -d -p 8978:8978 --name law-links-container law-links-service
sleep 2   # подождать запуск сервиса
curl -X POST http://localhost:8978/detect -H "Content-Type: application/json" \
     -d '{"text": "Согласно статье 23 Налогового кодекса и ст. 145 Гражданского кодекса"}'
```

CI (`.github/workflows/ci.yml`) для `homework_1` прогоняет тесты, оценку и замер скорости, собирает образ и делает запрос к `/detect`.

## Авторы

Исаева Диана, Селюшкина Наталья, Кунашева Илана, Вишневская Мелания, Демина Ксения.
