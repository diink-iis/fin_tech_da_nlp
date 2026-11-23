(() => {
  'use strict';

  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
  const state = {
    users: [], userId: null, filters: [], feeds: [], catalog: [], news: [],
    offset: 0, limit: 50, hasMore: false, newsLoading: false, profileLoading: false,
    view: 'news', filterView: 'all', stream: null, refreshTimer: null,
    channel: null, channels: [], epoch: 0,
    requests: { filters: 0, feeds: 0, news: 0, channel: 0 },
  };
  let noticeTimer = null;

  const safe = (value = '') => String(value ?? '').replace(/[&<>"']/g,
    character => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[character]));
  const parseList = value => String(value || '').split(/[\n,;]/).map(item => item.trim()).filter(Boolean);
  const terms = value => Array.isArray(value)
    ? value.filter(item => typeof item === 'string').map(item => item.trim()).filter(Boolean)
    : (typeof value === 'string' ? parseList(value) : []);
  const examples = value => Array.isArray(value)
    ? terms(value) : (typeof value === 'string' && value.trim() ? [value.trim()] : []);
  const selectedUser = () => state.users.find(user => String(user.id) === String(state.userId));
  const userPath = (userId = state.userId) => `/api/users/${userId}`;
  const channelKey = userId => `newsflow_channel_${userId}`;
  const sameProfile = (userId, epoch) => state.userId === userId && state.epoch === epoch;

  function storageGet(key) {
    try { return localStorage.getItem(key); } catch { return null; }
  }
  function storageSet(key, value) {
    try { localStorage.setItem(key, String(value)); } catch { /* Storage may be disabled. */ }
  }
  async function api(path, options = {}) {
    const response = await fetch(path, {
      ...options,
      headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
    });
    const text = await response.text();
    let data;
    try { data = text ? JSON.parse(text) : {}; } catch { data = text; }
    if (!response.ok) {
      let message = typeof data === 'string' ? data : (data.detail ?? data.message ?? `HTTP ${response.status}`);
      if (Array.isArray(message)) message = message.map(item => item.msg || JSON.stringify(item)).join('; ');
      throw new Error(typeof message === 'string' ? message : JSON.stringify(message));
    }
    return data;
  }
  function clearNotice() {
    clearTimeout(noticeTimer);
    $('#notice').classList.add('hidden');
    $$('.form-notice').forEach(element => { element.textContent = ''; element.classList.add('hidden'); });
  }
  function notify(message, type = 'info') {
    clearTimeout(noticeTimer);
    const notice = $('#notice');
    notice.className = `notice ${type}`;
    notice.textContent = message;
    const inline = $('dialog[open] .form-notice');
    if (inline) {
      inline.textContent = message;
      inline.className = `form-notice ${type}`;
    }
    if (type === 'success') noticeTimer = setTimeout(() => notice.classList.add('hidden'), 4000);
  }
  async function run(action) {
    try { await action(); } catch (error) { notify(error.message || 'Не удалось выполнить действие.', 'error'); }
  }
  async function submitOnce(form, action) {
    if (form.dataset.submitting === 'true') return;
    form.dataset.submitting = 'true';
    const buttons = $$('button[type="submit"]', form);
    buttons.forEach(button => { button.disabled = true; });
    try { await run(action); }
    finally {
      delete form.dataset.submitting;
      buttons.forEach(button => { button.disabled = false; });
    }
  }
  function requireUser() {
    if (!state.userId) throw new Error('Сначала создайте или выберите пользователя.');
    return state.userId;
  }
  function ensureUser() {
    if (state.userId) return true;
    openUserDialog();
    return false;
  }
  function requestScope(kind) {
    return { userId: state.userId, epoch: state.epoch, kind, id: ++state.requests[kind] };
  }
  function isCurrent(scope) {
    return sameProfile(scope.userId, scope.epoch) && state.requests[scope.kind] === scope.id;
  }
  async function loadScoped(kind, fetchData, apply) {
    const scope = requestScope(kind);
    try {
      const result = await fetchData(scope.userId);
      if (isCurrent(scope)) apply(result);
    } catch (error) { if (isCurrent(scope)) throw error; }
  }

  async function setActiveView(view) {
    if (!['news', 'filters', 'feeds', 'channel'].includes(view)) return;
    state.view = view;
    $$('.nav-item').forEach(button => button.classList.toggle('active', button.dataset.view === view));
    ['news', 'filters', 'feeds', 'channel'].forEach(name => $(`#${name}View`).classList.toggle('hidden', name !== view));
    const titles = {
      news: ['ВАША ПЕРСОНАЛЬНАЯ ЛЕНТА', 'Новости для вас'],
      filters: ['ПЕРСОНАЛИЗАЦИЯ', 'Мои фильтры'],
      feeds: ['RSS-ИСТОЧНИКИ', 'Источники'],
      channel: ['ВАШ RSS-КАНАЛ', 'Мой канал'],
    };
    $('#viewEyebrow').textContent = titles[view][0];
    $('#viewTitle').textContent = titles[view][1];
    $('#welcomeStrip').classList.toggle('hidden', view !== 'news');
    $('#newsToolbar').classList.toggle('hidden', view !== 'news');
    clearNotice();
    if (view === 'news') await loadNews(true);
    if (view === 'filters') await loadFilters();
    if (view === 'feeds') await loadFeeds();
    if (view === 'channel') await loadChannel();
  }
  async function loadUsers(preferredId = null) {
    state.users = await api('/api/users');
    const select = $('#userSelect');
    select.innerHTML = state.users.map(user => `<option value="${safe(user.id)}">${safe(user.name)}</option>`).join('');
    if (!state.users.length) {
      select.innerHTML = '<option value="">Пока нет пользователей</option>';
      return null;
    }
    const remembered = preferredId ?? storageGet('newsflow_user');
    const target = state.users.find(user => String(user.id) === String(remembered)) || state.users[0];
    select.value = target.id;
    return target.id;
  }
  function updateUserHint() {
    const user = selectedUser();
    $('#userHint').textContent = user ? `Активный профиль · ${user.name}` : 'Создайте пользователя, чтобы начать';
    $('#avatar').textContent = (user?.name || 'N').trim().charAt(0).toUpperCase();
  }
  async function changeUser(id) {
    closeStream();
    const userId = id ? Number(id) : null;
    const epoch = ++state.epoch;
    state.userId = userId;
    storageSet('newsflow_user', userId ?? '');
    state.filters = []; state.feeds = []; state.catalog = []; state.news = [];
    state.channel = null; state.channels = []; state.offset = 0;
    state.hasMore = false; state.newsLoading = false; state.filterView = 'all';
    state.profileLoading = Boolean(userId);
    $('#postForm').reset();
    $$('.filter-chip').forEach(button => button.classList.toggle('selected', button.dataset.filterView === 'all'));
    $('#lastUpdated').textContent = '';
    clearNotice(); updateUserHint(); renderFilters(); renderFeeds(); renderNews(); renderChannel();
    if (!userId) return;
    try { await Promise.all([loadFilters(), loadFeeds(), loadChannel()]); }
    finally {
      if (sameProfile(userId, epoch)) { state.profileLoading = false; renderNews(); }
    }
    if (!sameProfile(userId, epoch)) return;
    openStream();
    await loadNews(true);
  }
  async function createUser(name) {
    if (!name.trim()) throw new Error('Укажите имя пользователя.');
    const user = await api('/api/users', { method: 'POST', body: JSON.stringify({ name: name.trim() }) });
    const selectedId = await loadUsers(user.id);
    await changeUser(selectedId);
    notify(`Профиль «${user.name}» создан`, 'success');
  }
  async function loadFilters() {
    if (!state.userId) { state.filters = []; renderFilters(); return; }
    await loadScoped('filters', userId => api(`${userPath(userId)}/filters`), rows => {
      state.filters = rows; renderFilters();
    });
  }
  async function loadFeeds() {
    if (!state.userId) { state.feeds = []; state.catalog = []; renderFeeds(); return; }
    await loadScoped('feeds', userId => Promise.all([api(`${userPath(userId)}/feeds`), api('/api/feeds')]), result => {
      [state.feeds, state.catalog] = result;
      renderFeeds();
    });
  }
  async function loadNews(reset = false) {
    if (!state.userId) { state.news = []; state.hasMore = false; renderNews(); return; }
    if (!reset && (state.newsLoading || !state.hasMore)) return;
    const scope = requestScope('news');
    const offset = reset ? 0 : state.offset;
    if (reset) { state.news = []; state.offset = 0; state.hasMore = false; }
    state.newsLoading = true;
    renderNews();
    try {
      const rows = await api(`${userPath(scope.userId)}/news?limit=${state.limit}&offset=${offset}`);
      if (!isCurrent(scope)) return;
      const previous = reset ? [] : state.news;
      const seen = new Set(previous.map(article => article.id));
      state.news = [...previous, ...rows.filter(article => !seen.has(article.id))];
      state.offset = offset + rows.length;
      state.hasMore = rows.length === state.limit;
      $('#lastUpdated').textContent = `Обновлено ${new Date().toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' })}`;
    } catch (error) { if (isCurrent(scope)) throw new Error(`Не удалось загрузить ленту: ${error.message}`); }
    finally {
      if (isCurrent(scope)) { state.newsLoading = false; renderNews(); }
    }
  }

  function empty(icon, title, description, button, action) {
    return `<div class="empty-state"><div class="empty-icon">${icon}</div><h3>${safe(title)}</h3><p>${safe(description)}</p>${button ? `<button class="button primary" data-action="${action}">${safe(button)}</button>` : ''}</div>`;
  }
  function renderNews() {
    const list = $('#newsList');
    const rows = state.news.filter(article => state.filterView === 'all' || (article.matches || []).some(match => match.kind === state.filterView));
    $('#newsCount').textContent = state.news.length || '';
    const more = $('#loadMore');
    more.classList.toggle('hidden', !state.userId || !state.filters.length || !state.hasMore);
    more.disabled = state.newsLoading;
    more.textContent = state.newsLoading ? 'Загрузка…' : 'Показать ещё';
    if (!state.userId) {
      list.innerHTML = empty('👤', 'Создайте пользователя', 'Начните с профиля, затем добавьте подписки и фильтры.', 'Добавить пользователя', 'addUser'); return;
    }
    if (state.profileLoading || (state.newsLoading && !state.news.length)) {
      list.innerHTML = empty('↻', 'Загружаем ленту', 'Получаем новости выбранного профиля.', '', ''); return;
    }
    if (!state.filters.length) {
      list.innerHTML = empty('⌕', 'Добавьте первый фильтр', 'Лента показывает только новости, совпавшие хотя бы с одним вашим фильтром.', 'Создать фильтр', 'newFilter'); return;
    }
    if (!rows.length && state.hasMore) {
      list.innerHTML = empty('⌕', 'В загруженной части совпадений нет', 'Нажмите «Показать ещё», чтобы проверить более ранние новости, или выберите «Все совпадения».', '', ''); return;
    }
    if (!rows.length) {
      list.innerHTML = empty('✦', 'Пока нет совпадений', 'Добавьте RSS-источники, измените фильтры или обновите ленты.', 'Добавить источник', 'addFeed'); return;
    }
    list.innerHTML = rows.map(articleCard).join('');
  }
  function articleUrl(value) {
    if (!value) return null;
    try {
      const url = new URL(value, location.origin);
      return ['http:', 'https:'].includes(url.protocol) ? url.href : null;
    } catch { return null; }
  }
  function articleCard(article) {
    const matches = article.matches || [];
    const source = (state.feeds.find(feed => feed.id === article.feed_id) || state.catalog.find(feed => feed.id === article.feed_id) || {}).title || 'RSS источник';
    const date = article.published_at ? new Date(article.published_at).toLocaleString('ru-RU', { dateStyle: 'medium', timeStyle: 'short' }) : '';
    const badges = matches.map(match => `<span class="match-badge ${match.kind === 'topic' ? 'topic' : ''}">${match.kind === 'topic' ? '✦' : '⌕'} ${safe(match.filter_name)}</span>`).join('');
    const url = articleUrl(article.link);
    const title = safe(article.title || 'Без заголовка');
    const heading = url ? `<a href="${safe(url)}" target="_blank" rel="noopener noreferrer">${title}</a>` : title;
    return `<article class="news-card"><div class="news-card-top"><span class="source">${safe(source)}</span><span class="date">${safe(date)}</span></div><h3>${heading}</h3>${article.summary ? `<p class="news-summary">${safe(article.summary)}</p>` : ''}<div class="badge-row">${badges}</div><div class="reason-list">${matches.map(reasonHTML).join('')}</div></article>`;
  }
  function reasonHTML(match) {
    const details = match.details || {};
    if (match.kind === 'topic') {
      const score = Number(details.score || 0);
      const threshold = Number(details.threshold ?? 0.45);
      const phrase = details.best_phrase ? ` · Близкая формулировка: <strong>${safe(details.best_phrase)}</strong>` : '';
      return `<div class="reason"><strong>✦ ${safe(match.filter_name)}</strong> — семантическое сходство ${score.toFixed(3)} <span class="score-meter"><i style="width:${Math.max(0, Math.min(100, (score + 1) * 50))}%"></i></span> порог ${threshold.toFixed(2)}${phrase}</div>`;
    }
    const matched = terms(details.matched);
    const absent = terms(details.absent);
    const explanations = [];
    if (matched.length) explanations.push(`совпало: <strong>${safe(matched.join(', '))}</strong>`);
    if (absent.length) explanations.push(`не содержит: <strong>${safe(absent.join(', '))}</strong>`);
    const fields = terms(details.in || details.field).map(field => ({ title: 'заголовок', summary: 'текст' }[field] || field));
    if (fields.length) explanations.push(`область: ${safe(fields.join(', '))}`);
    if (details.criteria && typeof details.criteria === 'object') {
      const rules = [['any', 'любое из'], ['all', 'все'], ['none', 'исключить']]
        .map(([key, label]) => terms(details.criteria[key]).length ? `${label}: ${terms(details.criteria[key]).join(', ')}` : '').filter(Boolean);
      if (rules.length) explanations.push(`условия: ${safe(rules.join('; '))}`);
    } else if (typeof details.criteria === 'string' && details.criteria) explanations.push(safe(details.criteria));
    return `<div class="reason"><strong>⌕ ${safe(match.filter_name)}</strong> — ${explanations.join(' · ') || 'совпадение по заданным условиям'}</div>`;
  }
  function renderFilters() {
    const list = $('#filtersList');
    if (!state.userId) { list.innerHTML = empty('👤', 'Нужен профиль', 'Создайте пользователя, чтобы управлять фильтрами.', 'Добавить пользователя', 'addUser'); return; }
    if (!state.filters.length) { list.innerHTML = empty('⌕', 'Фильтров пока нет', 'Создайте фильтр по словам или теме.', 'Создать фильтр', 'newFilter'); return; }
    list.innerHTML = state.filters.map(filter => {
      const params = filter.params || {};
      const description = filter.kind === 'topic' ? (params.description || examples(params.examples).join('; '))
        : [['any', 'OR'], ['all', 'AND'], ['none', 'NOT']].flatMap(([key, label]) => terms(params[key]).map(term => `${label}: ${term}`)).join(' · ');
      const fields = params.fields == null ? ['title', 'summary'] : terms(params.fields);
      return `<div class="filter-card"><div class="filter-icon ${filter.kind === 'topic' ? 'topic' : ''}">${filter.kind === 'topic' ? '✦' : '⌕'}</div><div class="filter-main"><h3>${safe(filter.name)}</h3><p>${safe(description || 'Без описания')}</p><div class="filter-meta"><span class="mini-tag">${filter.kind === 'topic' ? 'Семантический' : 'Ключевые слова'}</span>${filter.kind === 'topic' ? `<span class="mini-tag">Порог ${Number(params.threshold ?? .45).toFixed(2)}</span>` : `<span class="mini-tag">Поля: ${safe(fields.join(', '))}</span>`}</div></div><div class="card-actions"><button class="small-action" data-edit-filter="${filter.id}">Изменить</button><button class="small-action" data-delete-filter="${filter.id}">Удалить</button></div></div>`;
    }).join('');
  }
  function renderFeeds() {
    const list = $('#feedsList');
    if (!state.userId) {
      list.innerHTML = empty('👤', 'Нужен профиль', 'Создайте пользователя для управления RSS.', 'Добавить пользователя', 'addUser');
      $('#catalogFeeds').innerHTML = ''; return;
    }
    list.innerHTML = state.feeds.length ? state.feeds.map(feed => `<div class="feed-card"><div class="feed-icon">◉</div><div class="feed-main"><h3>${safe(feed.title || 'RSS источник')}</h3><p>${safe(feed.url)}</p></div><button class="small-action" data-unsubscribe="${feed.id}">Отписаться</button></div>`).join('')
      : empty('◉', 'Нет подписок', 'Добавьте RSS-адрес или выберите источник из списка ниже.', 'Добавить RSS', 'addFeed');
    const subscribed = new Set(state.feeds.map(feed => feed.id));
    const other = state.catalog.filter(feed => !subscribed.has(feed.id));
    $('#catalogFeeds').innerHTML = other.length ? other.map(feed => `<div class="catalog-row"><div><strong>${safe(feed.title || 'RSS источник')}</strong><small>${safe(feed.url)}</small></div><button class="small-action" data-subscribe="${feed.id}">Подписаться</button></div>`).join('') : '<p class="muted small">Все известные источники уже добавлены в подписки.</p>';
  }

  function openFilterDialog(filter = null) {
    if (!ensureUser()) return;
    clearNotice();
    const form = $('#filterForm');
    form.reset();
    form.elements.id.value = filter?.id || '';
    form.elements.name.value = filter?.name || '';
    form.elements.kind.value = filter?.kind || 'keyword';
    const params = filter?.params || {};
    for (const key of ['any', 'all', 'none']) form.elements[key].value = terms(params[key]).join(', ');
    const fields = params.fields == null ? ['title', 'summary'] : terms(params.fields);
    $$('input[name="fields"]', form).forEach(input => { input.checked = fields.includes(input.value); });
    form.elements.description.value = params.description || '';
    form.elements.examples.value = examples(params.examples).join('\n');
    form.elements.threshold.value = params.threshold ?? .45;
    form.elements.aggregate.value = params.aggregate || 'mean';
    $('#thresholdValue').textContent = Number(form.elements.threshold.value).toFixed(2);
    $('#filterDialogTitle').textContent = filter ? 'Редактировать фильтр' : 'Новый фильтр';
    toggleFilterFields();
    $('#filterDialog').showModal();
  }
  function toggleFilterFields() {
    const topic = $('#filterKind').value === 'topic';
    $('#topicFields').classList.toggle('hidden', !topic);
    $('#keywordFields').classList.toggle('hidden', topic);
    $$('input,textarea,select', $('#topicFields')).forEach(input => { input.disabled = !topic; });
    $$('input,textarea,select', $('#keywordFields')).forEach(input => { input.disabled = topic; });
  }
  async function saveFilter(form) {
    const userId = requireUser(); const epoch = state.epoch;
    const id = form.elements.id.value;
    const kind = form.elements.kind.value;
    let params;
    if (kind === 'keyword') {
      params = { any: parseList(form.elements.any.value), all: parseList(form.elements.all.value), none: parseList(form.elements.none.value), fields: $$('input[name="fields"]', form).filter(input => input.checked).map(input => input.value) };
      if (!params.any.length && !params.all.length && !params.none.length) throw new Error('Заполните хотя бы одно поле any, all или none.');
      if (!params.fields.length) throw new Error('Выберите поле поиска.');
    } else {
      params = { description: form.elements.description.value.trim(), examples: form.elements.examples.value.split(/\n+/).map(value => value.trim()).filter(Boolean), threshold: Number(form.elements.threshold.value), aggregate: form.elements.aggregate.value };
      if (!params.description && !params.examples.length) throw new Error('Добавьте описание темы или примеры.');
    }
    const payload = { name: form.elements.name.value.trim(), kind, params };
    if (!payload.name) throw new Error('Укажите название фильтра.');
    await api(`${userPath(userId)}/filters${id ? `/${id}` : ''}`, { method: id ? 'PUT' : 'POST', body: JSON.stringify(payload) });
    $('#filterDialog').close();
    if (!sameProfile(userId, epoch)) return;
    await loadFilters(); await loadNews(true);
    notify(id ? 'Фильтр обновлён' : 'Фильтр создан. Лента пересчитана.', 'success');
  }
  function openSimpleDialog(title, fields, handler) {
    clearNotice();
    $('#simpleTitle').textContent = title;
    $('#simpleFields').innerHTML = fields;
    $('#simpleForm').onsubmit = event => {
      event.preventDefault();
      const form = event.currentTarget;
      void submitOnce(form, async () => { await handler(new FormData(form)); $('#simpleDialog').close(); });
    };
    $('#simpleDialog').showModal();
  }
  function openUserDialog() {
    openSimpleDialog('Новый пользователь', '<label>Имя<input name="name" required maxlength="100" placeholder="Как вас зовут?"></label>', data => createUser(data.get('name').trim()));
  }
  function openFeedDialog() {
    if (!ensureUser()) return;
    openSimpleDialog('Добавить RSS-источник', '<label>Ссылка на RSS или Atom<input name="url" inputmode="url" required placeholder="https://example.com/feed.xml"></label>', data => addFeedUrl(data.get('url').trim()));
  }
  async function addFeedUrl(url) {
    const userId = requireUser(); const epoch = state.epoch;
    if (!url) throw new Error('Введите RSS-ссылку.');
    await api(`${userPath(userId)}/feeds`, { method: 'POST', body: JSON.stringify({ url }) });
    if (!sameProfile(userId, epoch)) return;
    await loadFeeds(); await loadNews(true); notify('RSS добавлен в подписки.', 'success');
  }
  async function subscribe(feedId) {
    const userId = requireUser(); const epoch = state.epoch;
    await api(`${userPath(userId)}/feeds`, { method: 'POST', body: JSON.stringify({ feed_id: Number(feedId) }) });
    if (!sameProfile(userId, epoch)) return;
    await loadFeeds(); await loadNews(true); notify('Подписка добавлена.', 'success');
  }
  async function unsubscribe(feedId) {
    const userId = requireUser(); const epoch = state.epoch;
    await api(`${userPath(userId)}/feeds/${feedId}`, { method: 'DELETE' });
    if (!sameProfile(userId, epoch)) return;
    await loadFeeds(); await loadNews(true); notify('Вы отписались от источника.', 'success');
  }
  async function deleteFilter(filterId) {
    const userId = requireUser(); const epoch = state.epoch;
    await api(`${userPath(userId)}/filters/${filterId}`, { method: 'DELETE' });
    if (!sameProfile(userId, epoch)) return;
    await loadFilters(); await loadNews(true); notify('Фильтр удалён.', 'success');
  }
  async function refreshAll() {
    const button = $('#refreshBtn');
    if (button.disabled) return;
    button.disabled = true; button.textContent = '↻ Обновление…';
    try {
      const data = await api('/api/refresh', { method: 'POST' });
      await loadNews(true);
      const errors = (data.feeds || []).filter(feed => feed.error);
      if (errors.length) notify(`Обновлено: ${data.new || 0} новых. Ошибки ${errors.length} источников: ${errors.map(feed => feed.error).join(' | ')}`, 'error');
      else notify(`Источники обновлены. Новых статей: ${data.new || 0}.`, 'success');
    } finally { button.disabled = false; button.innerHTML = '<span>↻</span> Обновить источники'; }
  }
  function closeStream() {
    clearTimeout(state.refreshTimer);
    if (state.stream) state.stream.close();
    state.stream = null;
    $('#connectionText').textContent = 'Отключено';
    $('.live-dot').style.background = '#e5a64b';
  }
  function openStream() {
    closeStream();
    if (!state.userId) return;
    const userId = state.userId; const epoch = state.epoch;
    const stream = new EventSource(`${userPath(userId)}/stream`);
    state.stream = stream;
    const active = () => sameProfile(userId, epoch) && state.stream === stream;
    stream.addEventListener('refresh', () => {
      if (!active()) return;
      clearTimeout(state.refreshTimer);
      state.refreshTimer = setTimeout(() => {
        if (!active()) return;
        void run(async () => {
          await Promise.all([loadFilters(), loadFeeds()]);
          if (active() && state.view === 'news') await loadNews(true);
        });
      }, 350);
    });
    stream.onopen = () => { if (active()) { $('#connectionText').textContent = 'Обновления онлайн'; $('.live-dot').style.background = '#31c48a'; } };
    stream.onerror = () => { if (active()) { $('#connectionText').textContent = 'Переподключение…'; $('.live-dot').style.background = '#e5a64b'; } };
  }
  function renderChannel() {
    $('#channelEmpty').innerHTML = !state.userId ? empty('👤', 'Нужен профиль', 'Создайте пользователя, чтобы создать канал и публиковать статьи.', 'Добавить пользователя', 'addUser') : '';
    $('#channelSetup').classList.toggle('hidden', !state.userId || Boolean(state.channel));
    $('#postPanel').classList.toggle('hidden', !state.userId || !state.channel);
    if (state.channel) showChannel(state.channel);
  }
  async function loadChannel() {
    if (!state.userId) { state.channel = null; state.channels = []; renderChannel(); return; }
    await loadScoped('channel', () => api('/api/channels'), rows => {
      state.channels = rows;
      const remembered = storageGet(channelKey(state.userId));
      const own = rows.filter(channel => Number(channel.owner_user_id) === state.userId);
      state.channel = own.find(channel => String(channel.id) === remembered) || own[0]
        || rows.find(channel => channel.owner_user_id == null && String(channel.id) === remembered) || null;
      if (state.channel) storageSet(channelKey(state.userId), state.channel.id);
      renderChannel();
    });
  }
  function showChannel(channel) {
    $('#channelTitle').textContent = channel.title;
    $('#channelDescription').textContent = channel.description || 'Ваш канал';
    $('#channelUrl').textContent = channel.rss_url || `${location.origin}/rss/${channel.id}.xml`;
  }
  async function createChannel(form) {
    const userId = requireUser(); const epoch = state.epoch;
    const data = new FormData(form);
    const payload = { title: data.get('title').trim(), description: data.get('description') || '', user_id: userId };
    if (!payload.title) throw new Error('Укажите название канала.');
    const channel = await api('/api/channels', { method: 'POST', body: JSON.stringify(payload) });
    storageSet(channelKey(userId), channel.id);
    if (!sameProfile(userId, epoch)) return;
    state.channel = channel; renderChannel(); form.reset();
    await loadFeeds(); notify('Канал создан, вы подписаны на него.', 'success');
  }
  async function publishPost(form) {
    const userId = requireUser(); const epoch = state.epoch;
    const channelId = state.channel?.id;
    if (!channelId) throw new Error('Сначала создайте канал.');
    const data = new FormData(form);
    const payload = { title: data.get('title').trim(), text: data.get('text') || '', link: data.get('link') || null };
    if (!payload.title) throw new Error('Укажите заголовок публикации.');
    const post = await api(`/api/channels/${channelId}/articles`, { method: 'POST', body: JSON.stringify(payload) });
    if (!sameProfile(userId, epoch) || state.channel?.id !== channelId) return;
    form.reset();
    try { await loadNews(true); }
    catch (error) { notify(`Публикация сохранена. ${error.message}`, 'error'); return; }
    notify(`Публикация добавлена в канал${post.new_articles === 0 ? ' (уже была в ленте)' : ''}.`, 'success');
  }
  async function copyRSS() {
    try { await navigator.clipboard.writeText($('#channelUrl').textContent); notify('RSS-ссылка скопирована.', 'success'); }
    catch { notify('Не удалось скопировать автоматически. Выделите ссылку вручную.', 'info'); }
  }

  document.addEventListener('click', event => {
    void run(async () => {
      const target = event.target;
      const nav = target.closest('[data-view]'); if (nav) await setActiveView(nav.dataset.view);
      const open = target.closest('[data-open]'); if (open) await setActiveView(open.dataset.open);
      const close = target.closest('[data-close]'); if (close) $(`#${close.dataset.close}`).close();
      const action = target.closest('[data-action]')?.dataset.action;
      if (action === 'addUser') openUserDialog();
      if (action === 'newFilter') openFilterDialog();
      if (action === 'addFeed') openFeedDialog();
      const edit = target.closest('[data-edit-filter]');
      if (edit) { const filter = state.filters.find(item => String(item.id) === edit.dataset.editFilter); if (filter) openFilterDialog(filter); }
      const remove = target.closest('[data-delete-filter]');
      if (remove && confirm('Удалить фильтр?')) await deleteFilter(remove.dataset.deleteFilter);
      const unsub = target.closest('[data-unsubscribe]'); if (unsub) await unsubscribe(unsub.dataset.unsubscribe);
      const sub = target.closest('[data-subscribe]'); if (sub) await subscribe(sub.dataset.subscribe);
    });
  });
  $('#userSelect').addEventListener('change', event => { void run(() => changeUser(event.target.value)); });
  $('#addUserBtn').addEventListener('click', openUserDialog);
  $('#newFilterBtn').addEventListener('click', () => openFilterDialog());
  $('#addFeedBtn').addEventListener('click', openFeedDialog);
  $('#filterKind').addEventListener('change', toggleFilterFields);
  $('#filterForm').addEventListener('submit', event => { event.preventDefault(); const form = event.currentTarget; void submitOnce(form, () => saveFilter(form)); });
  $('#filterForm').elements.threshold.addEventListener('input', event => { $('#thresholdValue').textContent = Number(event.target.value).toFixed(2); });
  $('#channelForm').addEventListener('submit', event => { event.preventDefault(); const form = event.currentTarget; void submitOnce(form, () => createChannel(form)); });
  $('#postForm').addEventListener('submit', event => { event.preventDefault(); const form = event.currentTarget; void submitOnce(form, () => publishPost(form)); });
  $('#refreshBtn').addEventListener('click', () => { void run(refreshAll); });
  $('#loadMore').addEventListener('click', () => { void run(() => loadNews(false)); });
  $('#copyRss').addEventListener('click', () => { void run(copyRSS); });
  $$('.filter-chip').forEach(button => button.addEventListener('click', () => {
    state.filterView = button.dataset.filterView;
    $$('.filter-chip').forEach(item => item.classList.toggle('selected', item === button));
    renderNews();
  }));
  window.addEventListener('beforeunload', closeStream);
  void run(async () => { const userId = await loadUsers(); await changeUser(userId); });
})();
