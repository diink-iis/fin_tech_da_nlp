# Роль 5 — установка интерфейса

## Файлы, добавленные ролью 5

- `app/ui.py` — подключение UI-роутов.
- `app/templates/index.html` — HTML страница.
- `app/static/css/style.css` — стили.
- `app/static/js/app.js` — frontend логика/API/SSE.
- `tests/test_role5_ui.py` — UI smoke и сквозной API тест.
- `docs/role5_acceptance.md` — ручной сценарий приёмки.
- `docs/role5_presentation.md` — тезисы для слайдов.


## В интеграции изменён `app/main.py`

В него добавлено:

```python
from app.ui import install_ui_routes
```

и перед `return app` в `create_app()`:

```python
install_ui_routes(app)
```

В предоставленном архиве это уже добавлено. Если копируете только role5-files, добавьте эти две вставки самостоятельно.

## Запуск

Из корня проекта (используйте Python 3.12, как в командном README):

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt fastapi uvicorn pytest
python -m scripts.prepare_semantic
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Откройте `http://127.0.0.1:8000/`.

Первая загрузка semantic-модели требует сети. Для автономной демонстрации заранее прогрейте/сохраните модель и проверьте запуск с `HF_HUB_OFFLINE=1`.

## Тесты

```bash
python -m pytest -q tests/test_role5_ui.py
python -m pytest -q
```

Тесты UI проверяют раздачу HTML и static, а также сценарий API двух пользователей, собственный RSS канал, пост, фильтрацию, изоляцию и обновление фильтра. Полный набор тестов нужно выполнять в проектном окружении с установленными зависимостями из requirements.

## Особенности UI

- Без пользователей и фильтров показываются поясняющие empty states.
- Фильтры keyword и topic редактируются в одном диалоге.
- Списки ключевых слов принимают запятые, точку с запятой или отдельные строки.
- Topic отображает similarity score, threshold и best phrase, если их вернул движок.
- Фильтры OR друг с другом; keyword `any/all/none` как в API.
- EventSource подписан на named event `refresh`; UI обновляет news API, не переподключая поток на каждую перерисовку.
- RSS URL пользователь может добавить или выбрать уже известный feed.
- Вкладка «Мой канал» сохраняет выбранный id канала в браузере и предлагает форму постинга.
- UI не выводит API-строки через небезопасный HTML напрямую: пользовательские тексты экранируются при рендере.
