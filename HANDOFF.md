# HANDOFF.md — передача контекста в новую сессию

Снимок состояния проекта HepFeed на момент окончания сессии (2026-09-18).
Перманентная документация — в `docs/`; здесь — оперативный контекст для нового
агента/разработчика.

---

## 1. Цель

**HepFeed** — бот-агент, который:
1. мониторит свежие публикации по ФЭЧ (`hep-ex`, `hep-ph`) и физике
   ускорителей (`physics.acc-ph`) — arXiv, далее INSPIRE/CDS/RSS/институты;
2. дедуплицирует и обогащает каждую работу контекстом (сейчас: полный текст из
   HTML arXiv; план: INSPIRE-граф, Crossref, веб-поиск);
3. генерирует через облачную LLM лаконичную профессиональную заметку на
   русском (≤4096 символов, без популизма, честный блок «ограничения»);
4. публикует в Telegram-каналы (роутинг HEP/AP по категориям), ручная
   модерация — как опция.

**Источники истины:** `docs/CONCEPT.md` (scope, формат заметки §5, модули §6,
архитектура §7, метрики §14), `docs/SOURCES.md` (спецификация источников).
Если код противоречит им — документы главные.

---

## 2. Текущее состояние

### Репозиторий
- GitHub: `github.com/sstarostin1/HepFeed`, публичный, ветка `main`,
  последний коммит `289febe` (всё запушено, дерево чистое).
- Лицензия MIT (© Grestarideus). Линтер ruff, тесты pytest
  (**126 passed**, все офлайн — MockTransport/фейки, реальный API не трогают).
- Прекоммит-хуки активны: gitleaks + гигиена файлов.

### Сервер (деплой живой)
- Debian 13, root по `ssh vps` (алиас с ключом), код в `/opt/hepfeed`,
  коммит синхронизирован с `main`.
- Сервис **`hepfeed.service`**: `active (running)`, `enabled` (автостарт,
  `Restart=on-failure`), запускает `python -m hepfeed schedule`.
- Админ-консоль в личке бота активна (слушатель `hepfeed.admin`,
  только `TELEGRAM_MODERATOR_CHAT_ID=1439973172` — числовой ID!).
- `.env` на сервере выверен (история правок — в §5, п. 8).
- **Конвейер работает автономно**: за ночь опубликовано ~30 заметок;
  полнотекстовый режим включён (`LLM_USE_FULL_TEXT=true` по умолчанию).
- Состояние конвейера: SQLite `/opt/hepfeed/data/hepfeed.db`
  (статьи: seen → note_created/note_failed; заметки: ready →
  published/in_review/rejected; кэш полного текста — `full_text`).

### Интервалы задач (по умолчанию, переопределяются через `.env`)
- опрос arXiv: 360 мин (первый запуск — сразу при старте сервиса);
- генерация заметок: 15 мин; публикация: 10 мин.

### Модель/LLM
- Провайдер Polza.AI, OpenAI-совместимый:
  `POST https://polza.ai/api/v1/chat/completions`.
- Цепочка моделей (фолбэки по убыванию приоритета):
  `deepseek/deepseek-v4-flash-0731@provider=open-inference/fp8` →
  `...@provider=streamlake/fp8` → `...@provider=deepinfra/fp8`.
- `reasoning_effort: "low"` (принимается, ~3.5× быстрее дефолта,
  качество сопоставимо). Бюджет `max_tokens=10000` на заметку.
- Ключи — только в `.env`/env, никогда в git.

---

## 3. Активные файлы

### Документация
- `docs/CONCEPT.md` — концепция (§3 scope, §5 формат заметки, §6 модули,
  §7 стек, §12 дорожная карта, §15 открытые вопросы);
- `docs/SOURCES.md` — спецификация всех источников (API, лимиты, матрица покрытия);
- `docs/SECURITY_NOTE.md` — политика секретов + юридические оговорки (Sci-Hub и т.п.);
- `docs/DEPLOY.md` — гайд: пуш в репо, деплой, диагностика, откат;
- `deploy/hepfeed.service` — systemd-юнит;
- `AGENTS.md` — правила для AI-агентов; `CONTRIBUTING.md` — процесс.

### Код (`src/hepfeed/`)
- `config.py` — pydantic-settings; ключевые поля: `llm_model`,
  `llm_model_fallbacks`, `llm_reasoning_effort`, `llm_use_full_text`,
  `llm_full_text_max_chars`, интервалы задач, `publish_moderation`,
  каналы HEP/AP, `database_url`.
- `cli.py` — подкоманды: `check`, `poll-arxiv [--hours --dry-run]`,
  `generate-notes [--limit --dry-run]`,
  `publish [--limit --dry-run --approve ID --reject ID]`,
  `schedule [--interval-minutes]`.
- `scheduler.py` — APScheduler v3 (BlockingScheduler), 3 job'а с
  `PauseFlag` + поток `AdminListener`.
- `admin.py` — консоль оператора: long-polling getUpdates, команды
  `/status /pause /resume /run_poll /run_notes /run_publish /moderation on|off
  /prompt /prompt_set /prompt_reset /help` (без пробелов — кликабельны),
  авторизация только по модератор-чату; также обрабатывает `callback_query` от
  inline-кнопок модерации (`parse_moderation_callback`, `handle_callback`,
  `moderation_keyboard`). Runtime-переключатели: `PauseFlag`, `ModerationFlag`.
- `ingestion/` — `arxiv.py` (Atom API, ретраи 429/5xx/транспортных ошибок,
  окно свежести), `models.py` (PaperRecord, pydantic), `dedup.py`
  (ключи DOI → arXiv ID → title+author), `store.py` (SQLite: seen_papers +
  notes, миграции, статусы, full_text, stats), `pipeline.py` (цикл:
  fetch → окно → дедуп → persist).
- `enrichment/arxiv_html.py` — полный текст из нативного HTML-рендеринга
  arXiv, экстракция с фильтром мусора, кэшируется в БД.
- `generation/` — `llm.py` (цепочка моделей, ретраи, `extra_body`),
  `prompt.py` (системный промпт: 8 правил, №8 — про отсутствующий текст),
  `notes.py` (пост-проверки: ≤4096, arXiv-ссылка, запрещённые эмодзи),
  `pipeline.py` (pending → полный текст → LLM → статус).
- `publishing/` — `telegram.py` (sendMessage с опциональной inline-клавиатурой,
  уважает `retry_after`),
  `pipeline.py` (роутинг `physics.acc*` → AP-канал; модерация
  `PUBLISH_MODERATION=true` → оператору с кнопками, решение по callback,
  CLI `--approve/--reject ID`, `apply_moderation_decision_sync` для кнопок).
- `logging/setup.py` — формат логов, приглушение шума APScheduler.

### Служебное (локально, в `data/`, не в git)
- `experiment_effort.py`, `exp_*.txt` — артефакты A/B-эксперимента reasoning;
- `ft_test.py`, `seed_server.py` — серверные помощники для тестов;
- `system_prompt.txt` — текущий пользовательский промпт (`/prompt_set`);
- `system_prompt.prev.txt` — предыдущая версия промпта (для отката
  `/prompt_reset` / кнопки; создаётся при каждом сохранении);
- `.env` — реальные секреты (локально и на сервере; в git никогда).

---

## 4. Внесённые изменения (хронология сессии)

1. **Скелет репо для публичного GitHub**: README, LICENSE (MIT), AGENTS.md,
   CONTRIBUTING.md, .gitignore, .env.example, pre-commit (gitleaks),
   PR-шаблон; секрет из opencode.json убран из проекта (настройка вынесена
   глобально на уровне ПК).
2. **Скелет пакета**: pyproject (hatchling; extras: `pdf`=PyMuPDF,
   `db`=SQLAlchemy, `dev`=pytest/ruff/pre-commit), config, CLI `check`.
3. **Ingestion**: arXiv Atom-поллер + дедупликация + SQLite-хранилище;
   CLI `poll-arxiv`.
4. **Планировщик**: APScheduler, три периодические задачи, live-проверка.
5. **Генерация**: LLM-клиент, промпты, пост-проверки, жизненный цикл заметок
   в БД (статья: seen → note_created/note_failed; заметка: ready →
   published), CLI `generate-notes`.
6. **Публикация**: Telegram-клиент, роутинг HEP/AP, MVP-модерация
   (`--approve/--reject`), CLI `publish`.
7. **Фолбэк-цепочка моделей** (после деградации open-inference):
   streamlake, deepinfra; `reasoning_effort` конфигурируем.
8. **Полный текст в промпт**: enrichment-модуль, кэш в БД, guard пустого ввода.
9. **Админ-консоль**: /status /pause /resume /run, пауза-флаг в job'ах.
10. **Деплой на VPS**: клон, venv, systemd, гайд `docs/DEPLOY.md`.
11. Документация: DEPLOY.md, SECURITY_NOTE.md, разделы README.

---

## 4а. Обновление 2026-09-18, сессия 2 (эта сессия)

1. **Inline-кнопки модерации** (см. §6, п. 6) — сделано и задеплоено.
2. **Модерация выключена, runtime-переключатель**: `ModerationFlag`
   (стартовое значение — `PUBLISH_MODERATION`), команда `/moderation`
   (чистый toggle, без аргументов — кликабельна);
   `publish_notes_sync(..., moderation=...)` перекрывает env-значение.
3. **Команды без пробелов**: `/run_poll /run_notes /run_publish` вместо
   `/run poll|notes|publish` — для кликабельности из справки.
4. **Правка системного промпта из чата**: `/prompt` — промпт в копируемом
   HTML-блоке `<pre>` (чанки по 3400 символов, html.escape) с inline-кнопками
   «Откатить к предыдущей» (`prompt:rollback`) и «Как редактировать»
   (`prompt:edit_hint`), `/prompt_set` — reply с новой версией (снимаются
   ```-фенсы, сохраняется в `data/system_prompt.txt`), `/prompt_reset` и кнопка
   — откат к ПРЕДЫДУЩЕЙ версии (`system_prompt.prev.txt`, бэкап создаётся при
   каждом сохранении; встроенный промпт НЕ восстанавливается — он просто
   нулевая версия, по требованию оператора). Файл промпта не редактируется
   напрямую (затёрся бы `git pull`); оверрайд-файлы — вне git.
5. **Markdown → Telegram-HTML**: модель по правилу №9 промпта использует
   ограниченную разметку (**bold**, *italic*, __underline__, ~~strike~~,
   `code`, ```блоки```, [т](url), ||спойлер||, `> ` цитаты, `- ` списки);
   `publishing/markdown.py` конвертирует её в HTML (цитаты — в
   `<blockquote expandable>`), публикация и модерация уходят с
   `parse_mode=HTML`. Причина: Telegram не парсит markdown в plain text —
   `**жирный**` оставался звёздочками. Неизвестная разметка экранируется.
   Правило №10 запрещает LaTeX ($..$, \(..\), \frac, \begin и т.п.) — он в
   Telegram не рендерится, формулы — текстом и юникодом.

---

## 5. Список неудачных попыток (грабли — не наступать повторно)

1. **arXiv API 429 при серии запросов**: ~12 запросов за 10 минут → долгий
   кулдаун (и 429, и зависания ReadTimeout). Норма — 1 запрос/3с+; интервал
   6 ч безопасен. Ретраи в `ArxivClient` обязательны.
2. **Reasoning-петля LLM, три проявления**:
   - `max_tokens` мал (2000/6000) → reasoning съедает бюджет, `content`
     пустой (на дашборде OUT ровно = лимиту). Лечение: бюджет 10000 +
     `reasoning_effort: low`.
   - **Пустой ввод** (статья без текста/абстракта) → бесконечное рассуждение
     о противоречии «пиши ↔ не выдумывай» (один прогон сжёг 50k токенов, 0.59₽).
     Лечение: правило №8 в промпте + guard пустого абстракта в pipeline.
   - `chat_template_kwargs: {"thinking": false}` ПРИНИМАЕТСЯ, но reasoning
     НЕ отключает (в ответе всё равно ~24к символов размышлений).
3. **Провайдеры Polza**: `baidu/fp8` работает, но в ~14 раз дороже
   (0.98₽ против 0.07₽) — убран; `deepseek/fp8` — 400 «No allowed providers»
   для нашего ключа. Доступны: open-inference, inceptron, streamlake,
   relac… — новые фолбэки сверять с текстом этой ошибки.
4. **httpx: относительный URL без `base_url`** на внедрённом клиенте падает
   (cookie-парсер) — у всех клиентов явно задавать `base_url`.
5. **Authorization-заголовок терялся** при внедрённом httpx-клиенте —
   заголовок ставится per-request, а не на клиенте.
6. **Лаг git-эндпоинта GitHub**: `git pull` на сервере сразу после push
   может молча не видеть коммит — пауза 15–20 с или повторный `git fetch`.
7. **CRLF из Windows** ломает bash-скрипты на сервере
   (`set: -\r: invalid option`) — `sed -i "s/\r$//"` после scp.
8. **`.env` на сервере**: правки не доезжали (правили локальную копию);
   `chat_id` — только числовой (теги пользователей API не принимает);
   изменение `.env` требует `systemctl restart hepfeed`.
9. **Конфликт имён** `mark_note_status` (по paper для генерации и по note_id
   для публикации) — поймал ruff F811; публикационное переименовано в
   `mark_note_publish_status`.
10. **RUF001 (ambiguous unicode)** на кириллице в строках кода — per-file
    ignores для `generation/prompt.py` и `publishing/pipeline.py`.
11. **Наивная экстракция arXiv-HTML** тянет UI-обвязку страницы (баннеры,
    ToC, донат-блоки) — фильтр по маркерам + срез до «License:».
12. **Ошибочный запуск эксперимента с пустым текстом** (0.59₽ за 50k-цикл):
    провайдер дорисовывает генерацию даже после убийства клиента.
13. **Нестабильный захват терминала** при длинных ssh-сессиях (stale echo,
    код 255) — надёжнее: серверные скрипты с логами в файлы + polling.
14. **Тесты, звавшие async-функции напрямую** (корутина вместо результата) —
    использовать sync-обёртки (`*_sync`) или `asyncio.run`.

---

## 6. Конкретные следующие шаги разработки

1. **Тюнинг системного промпта** (отложен оператором «на потом»; теперь
   осмысленно с полным текстом): учить модель ВЫБИРАТЬ главное из статьи,
   а не пересказывать. Сравнить качество на 3–5 свежих статьях до/после.
   Отметить: оператор уже поднимал целевой объём до 2500–3500 символов
   (правило 5 промпта); `validate_note` жёстко проверяет только ≤4096.
2. **INSPIRE-граф** — блок «контекст» сейчас честно «не восстановлен»:
   запрос record по arXiv ID (`api.inspirehep.net`), references и
   предыдущие работы → подать в промпт как контекст
   (спецификация — `docs/SOURCES.md` §2.2).
3. **Crossref-верификация** метаданных (авторы/журнал/DOI) — §9 SOURCES.
4. **PDF-фолбэк** для статей без HTML-рендеринга: PyMuPDF в extras
   (`pip install -e ".[pdf]"` — extras ещё нигде не ставились).
   Осторожно: таблицы/формулы в извлечённом тексте — источник мусора.
5. **Backfill legacy-статей**: ~14 старых строк в БД сервера без
   `record_json` (записаны до миграции) — генерация их пропускает.
   Один запрос `id_list` к arXiv API вернёт метаданные; разовый скрипт
   обновления `record_json`.
6. ~~**Inline-кнопки модерации**~~ — **сделано (2026-09-18, локально, до деплоя
   проверено тестами, 105 passed)**: сообщения модерации отправляются с
   inline-клавиатурой («Опубликовать» / «Отклонить», callback_data
   `note:<id>:approve|reject`); слушатель `admin.py` обрабатывает `callback_query`
   (авторизация по `from.id` == модератор-чат), решение исполняется в фоновом
   потоке (`apply_moderation_decision_sync` в `publishing/pipeline.py`), после
   решения кнопки снимаются (`editMessageReplyMarkup`) и приходит статус-сообщение.
   Защита от повторных нажатий: статус заметки проверяется перед применением
   (действительно только для `ready`/`in_review`). CLI `--approve/--reject`
   сохранён как запасной путь.
7. **Тихий час** (CONCEPT §6.5): отложить публикацию ночных заметок до утра.
8. **Фильтрация/тегирование** — отложено оператором (сложная семантика),
   вернуться после стабилизации качества заметок.
9. **Наблюдение**: сутки авто-режима; `journalctl -u hepfeed`, `/status`,
   стоимость на дашборде Polza (сейчас ~0.04–0.16₽/заметка — сильно внутри
   бюджета ≤$0.10 из CONCEPT §14).
10. **Ротация ключа Polza** (опционально): ключ существовал в локальном
    opencode.json до санитизации; в git не попадал, но паранойя уместна.

### Быстрые команды для новой сессии

```bash
# локально: самопроверка
.venv/Scripts/python -m ruff format . && .venv/Scripts/python -m ruff check . && .venv/Scripts/python -m pytest

# сервер: обновление
git push && ssh vps 'cd /opt/hepfeed && git pull && systemctl restart hepfeed'
ssh vps 'journalctl -u hepfeed -n 20 --no-pager'

# сервер: разовые этапы (без сервиса — для отладки)
ssh vps 'cd /opt/hepfeed && .venv/bin/python -m hepfeed poll-arxiv --hours 24'
ssh vps 'cd /opt/hepfeed && .venv/bin/python -m hepfeed generate-notes --limit 2'
ssh vps 'cd /opt/hepfeed && .venv/bin/python -m hepfeed publish --limit 5'
```
