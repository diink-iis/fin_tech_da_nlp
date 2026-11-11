# Агрегатор и свой канал (роль 3)

Модули: `app/aggregator.py` (чтение лент), `app/channels.py` (свой RSS-канал).
Тесты: `tests/test_aggregator.py`, `tests/test_channels.py`.

Агрегатор не знает о фильтрах и пользователях: на входе адрес ленты, на выходе список `Article`.
Свой канал отдаётся приложением как обычный RSS 2.0, поэтому агрегатор читает его тем же кодом, что и чужие ленты.

```
внешние ленты ─┐
               ├─ HTTP ─► read_feed ─► parse_feed ─► [Article] ─► save (роль 4: БД, движки, SSE)
/rss/{id}.xml ─┘                          ▲
ChannelStore.render_rss ──────────────────┘  (свой канал можно разобрать и без HTTP)
```

## Агрегатор

```python
articles = fetch_feed(url, feed_id)          # контракт из PLAN.md: list[Article], без дублей
info = read_feed("lenta.ru/rss")             # FeedInfo(url, title, site_link, articles) — для «добавить канал»
info = parse_feed(xml_text, url, feed_id)    # то же без сети
results = refresh_feeds([(feed_id, url), ...], save)   # параллельный опрос, [RefreshResult]
poller = Poller(get_feeds, save, interval=300)         # фоновый опрос + refresh_now() для кнопки
```

Что делает `read_feed`:

- **Адрес как ввёл человек**: `lenta.ru/rss` превращается в `https://lenta.ru/rss`, допускаются только `http` и `https`.
- **Ссылка на сайт вместо ленты**: если по адресу HTML-страница, ищет на ней `<link rel="alternate" type="application/rss+xml">` и читает найденную ленту. Итоговый адрес — в `FeedInfo.url`, его и стоит сохранить.
- **Форматы**: RSS 0.9x/1.0/2.0 и Atom через `feedparser`; кодировку (в том числе `windows-1251`) определяет он же.
- **Ограничения**: таймаут 15 с, не больше 5 МБ.
- **Ошибки** (сеть, 404, не лента) — `FeedError` с текстом на русском, его можно показать пользователю как есть.

Как заполняется `Article`:

| Поле | Откуда |
|------|--------|
| `id` | `0`, настоящий назначает БД |
| `guid` | `guid`/`id` записи → ссылка → `sha1:` от заголовка и текста (устойчив между опросами) |
| `title`, `summary` | HTML убран, сущности раскрыты, `<script>` выброшен; анонс обрезан до 4000 символов; без заголовка — начало анонса |
| `link` | относительная ссылка раскрыта до полной |
| `published_at` | `published` → `updated`, всегда в UTC; без даты — время чтения |

Дубли по `guid` внутри одной ленты отбрасываются. Дубли между опросами отсекает БД уникальным ключом `(feed_id, guid)`.

### Опрос

`refresh_feeds(feeds, save)` читает ленты параллельно (до 8 сразу). `save(feed_id, articles)` пишет роль 4: сохраняет статьи и возвращает число **новых**.
Упавшая лента не мешает остальным: ошибка попадает в `RefreshResult.error`.

`Poller` вызывает `get_feeds()` перед каждым кругом, поэтому новый канал подхватывается без перезапуска.
Одновременно идёт только один опрос: кнопка «обновить» во время фонового опроса дождётся его окончания.

## Свой канал

```python
store = ChannelStore("news.db")                         # таблицы channels и channel_posts создаются сами
ch = store.create_channel("Тестовый канал", "описание")
store.post_article(ch.id, "Заголовок", "Текст", link=None)
store.render_rss(ch.id, base_url)                       # str, RSS 2.0 для GET /rss/{id}.xml
store.list_channels(), store.list_posts(ch.id), store.delete_post(ch.id, post_id)
channel_feed_url(base_url, ch.id)                       # http://host/rss/{id}.xml
```

- RSS 2.0 со всеми обязательными полями, `atom:link rel="self"`, `guid isPermaLink="false"` вида `channel-1-post-5`, даты в формате RFC 822 (GMT).
- Посты от новых к старым; без своей ссылки пост получает `.../rss/{id}.xml#post-{id}`.
- Проверки: заголовок обязателен (до 300 символов), текст до 20 000, ссылка только `http(s)://`. Ошибка — `ChannelError`.
- Данные в SQLite, переживают перезапуск (есть тест).

## Что нужно от других ролей

| Роль | Что |
|------|-----|
| 4 (бэкенд) | `save(feed_id, articles)`: `INSERT OR IGNORE` по уникальному `(feed_id, guid)`, вернуть число новых, для новых вызвать `SemanticEngine.index` и отправить событие в SSE |
| 4 | «добавить канал»: `read_feed(url)` → сохранить `info.url` и `info.title`, сразу сохранить `info.articles`, чтобы новости появились без ожидания опроса; `FeedError` → 400 с текстом |
| 4 | в `lifespan`: `ChannelStore(путь к БД)`, `Poller(...).start()`, при остановке `await poller.stop()`; `POST /api/refresh` → `await poller.refresh_now()` |
| 4 | маршруты `POST /api/channels`, `POST /api/channels/{c}/articles`, `GET /rss/{c}.xml` (`media_type="application/rss+xml"`, `ChannelError` → 404/400) |
| 4 | после создания канала добавить `channel_feed_url(base_url, id)` как обычную ленту и подписать на неё пользователя |
| 4 | после поста не ждать опроса: `save(feed_id, parse_feed(store.render_rss(c, base_url), url, feed_id).articles)` — пост появится в ленте сразу, без HTTP-запроса к самому себе |
| 5 (интерфейс) | форма поста (заголовок, текст, ссылка необязательна) и показ `FeedError`/`ChannelError` при добавлении канала; `title` и `summary` уже без HTML, выводить через `textContent` |

`base_url` — адрес, по которому приложение видно снаружи (`request.base_url`). Если сервис в Docker слушает другой порт, чем проброшен наружу, опрос своего канала по HTTP пойдёт мимо — поэтому после поста лучше разбирать RSS напрямую, как в таблице выше.

## Почему так

- **`feedparser`** — де-факто стандарт: RSS всех версий, Atom, кодировки, даты в разных форматах и часовых поясах, битые ленты. Самописный парсер на `xml.etree` ломался бы на первой нестандартной ленте.
- **`httpx` отдельно от `feedparser`**: свой таймаут, ограничение размера, редиректы, понятные ошибки и подмена транспорта в тестах (сеть в тестах не нужна).
- **Анонс без HTML** в БД: интерфейсу не нужно чистить чужую разметку, нет риска XSS; движки ролей 1 и 2 и так работают с текстом.
- **Свой канал — настоящий RSS по HTTP**, а не запись сразу в таблицу статей: так он проверяет весь путь агрегатора, и его можно открыть в любом RSS-ридере.

## Проверено на реальных лентах (2026-11-11)

Ведомости (200 статей), Хабр (40), Коммерсант (550), Медуза (30), BBC Russian (33) читаются за 2–8 с.
`lenta.ru/rss` из нашей сети не ответила за 15 с, `tass.ru` не указывает RSS на главной — в обоих случаях пользователь видит понятную ошибку.

## Запуск тестов

```bash
cd homework_2
pip install feedparser httpx pytest
python -m pytest -q tests/test_aggregator.py tests/test_channels.py
```
