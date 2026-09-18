# DEPLOY.md — ручной пуш в репозиторий и деплой на сервер

Практический гайд для solo-разработки. Политики (ветки, типы коммитов) — в
[CONTRIBUTING.md](../CONTRIBUTING.md); секреты — только через `.env`
([SECURITY_NOTE.md](SECURITY_NOTE.md)).

---

## 1. Пуш в репозиторий (штатный цикл)

```bash
# 1. Ветка под задачу
git checkout -b feat/<кратко>          # feat|fix|docs|chore

# 2. Работа + локальная самопроверка
.venv/Scripts/python -m ruff format .  # Windows Git Bash
.venv/Scripts/python -m ruff check .
.venv/Scripts/python -m pytest

# 3. Коммиты (Conventional Commits, один коммит = одна тема)
git add <файлы>
git commit -m "feat(scope): что сделано"

# 4. Вливание в main
git checkout main
git pull                                # подтянуть чужие/облачные изменения
git merge --no-ff feat/<кратко> -m "chore: merge feat/<кратко> (...)"
git push
```

**Грабли:** сразу после `git push` серверный `git pull` может не увидеть новый
коммит (кэш git-эндпоинта GitHub расходится на десятки секунд). Если pull
молча оставил старый `git log` — повторите pull через ~15–20 секунд или
сначала сделайте `git fetch` и проверьте `git log origin/main -1`.

---

## 2. Сервер: текущая схема

| Что | Где |
|---|---|
| Подключение | `ssh vps` (alias с ключом, root) |
| Код | `/opt/hepfeed` (клон репозитория) |
| Секреты | `/opt/hepfeed/.env` (права 600, никогда не в git) |
| Окружение | `/opt/hepfeed/.venv` (Python 3.13) |
| Состояние | `/opt/hepfeed/data/hepfeed.db` (SQLite) |
| Сервис | systemd `hepfeed.service` (`python -m hepfeed schedule`) |
| Управление ботом | команды в личке бота: `/status`, `/pause`, `/resume`, `/run poll\|notes\|publish` |

Системный юнит — [deploy/hepfeed.service](../deploy/hepfeed.service)
(автостарт, `Restart=on-failure`).

---

## 3. Первый деплой (с нуля)

```bash
ssh vps 'git clone https://github.com/sstarostin1/HepFeed.git /opt/hepfeed'
scp .env vps:/opt/hepfeed/.env && ssh vps 'chmod 600 /opt/hepfeed/.env'
ssh vps 'cd /opt/hepfeed && python3 -m venv .venv \
  && .venv/bin/pip install -q -e . && .venv/bin/python -m hepfeed check'
scp deploy/hepfeed.service vps:/etc/systemd/system/hepfeed.service
ssh vps 'sed -i "s/\r$//" /etc/systemd/system/hepfeed.service \
  && systemctl daemon-reload && systemctl enable --now hepfeed'
```

---

## 4. Обновление работающего сервера

```bash
git push                                      # локально, после merge в main
ssh vps 'cd /opt/hepfeed && git pull && systemctl restart hepfeed'
ssh vps 'journalctl -u hepfeed -n 20 --no-pager'   # убедиться, что стартовал
```

Если менялся `.env.example` — синхронизируйте значения вручную в
`/opt/hepfeed/.env` (файл не перетирается pull'ом).

---

## 5. Управление сервисом

```bash
ssh vps 'systemctl status hepfeed'            # состояние
ssh vps 'systemctl stop|start|restart hepfeed'
ssh vps 'journalctl -u hepfeed -f'            # живой журнал
ssh vps 'journalctl -u hepfeed -n 50 --no-pager'
```

`systemctl stop` — полная остановка (и планировщика, и админ-консоли).
`/pause` в Telegram — мягкая пауза: процессы живут, периодические задачи
пропускаются, ручные `/run ...` продолжают работать.

---

## 6. Диагностика и типовые ситуации

- **429 от arXiv** — штатно: задача логирует ошибку и ждёт следующего цикла;
  процесс не падает. Ручной прогон: `ssh vps 'cd /opt/hepfeed && \
  .venv/bin/python -m hepfeed poll-arxiv --hours 24'`.
- **Ручные разовые команды** (без сервиса): `generate-notes --limit N`,
  `publish --limit N` — полезно для отладки; при остановленном сервисе
  дубли публикаций невозможны.
- **CRLF при переносе скриптов** — скрипты, созданные на Windows, на сервере
  нужно нормализовать: `sed -i "s/\r$//" <файл>`.
- **Состояние БД** (статьи/заметки/статусы):
  ```bash
  ssh vps 'cd /opt/hepfeed && .venv/bin/python -c "import sqlite3; \
    conn=sqlite3.connect(\"data/hepfeed.db\"); \
    print(conn.execute(\"SELECT note_status, COUNT(*) FROM seen_papers GROUP BY 1\").fetchall()); \
    print(conn.execute(\"SELECT status, COUNT(*) FROM notes GROUP BY 1\").fetchall())"'
  ```

---

## 7. Откат

```bash
ssh vps 'cd /opt/hepfeed && git log --oneline -5'          # выбрать коммит
ssh vps 'cd /opt/hepfeed && git checkout <hash> && systemctl restart hepfeed'
```

Данные (`data/hepfeed.db`) при откате кода сохраняются; миграции схемы
добавляют колонки, но не удаляют — старый код с «лишними» колонками работает.
