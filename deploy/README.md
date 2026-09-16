# Деплой HepFeed на сервер (Debian, systemd)

Предполагается: root-доступ по SSH (`ssh vps`), Python 3.11+, git.

## Первый деплой

```bash
# 1. Код
ssh vps 'git clone https://github.com/sstarostin1/HepFeed.git /opt/hepfeed'

# 2. Секреты (файл .env НИКОГДА не попадает в репозиторий)
scp .env vps:/opt/hepfeed/.env
ssh vps 'chmod 600 /opt/hepfeed/.env'

# 3. Окружение
ssh vps 'cd /opt/hepfeed && python3 -m venv .venv && .venv/bin/pip install -q -e . && .venv/bin/python -m hepfeed check'

# 4. Сервис (из каталога репозитория)
scp deploy/hepfeed.service vps:/etc/systemd/system/hepfeed.service
ssh vps 'sed -i "s/\r$//" /etc/systemd/system/hepfeed.service \
  && systemctl daemon-reload \
  && systemctl enable --now hepfeed \
  && systemctl --no-pager status hepfeed'
```

## Обновление

```bash
git push                                    # локально, после merge в main
ssh vps 'cd /opt/hepfeed && git pull -q && systemctl restart hepfeed'
```

## Диагностика

```bash
ssh vps 'systemctl --no-pager status hepfeed'
ssh vps 'journalctl -u hepfeed -n 50 --no-pager'          # последние события
ssh vps 'tail -50 /opt/hepfeed/data/hepfeed.db' 2>/dev/null  # нет: см. ниже
ssh vps 'cd /opt/hepfeed && .venv/bin/python -m hepfeed check'
ssh vps 'cd /opt/hepfeed && .venv/bin/python -m hepfeed poll-arxiv --hours 24'
```

## Заметки

- Интервалы и секреты — в `/opt/hepfeed/.env` (шаблон: `.env.example`).
- Состояние конвейера — SQLite в `/opt/hepfeed/data/hepfeed.db`
  (статусы статей и заметок, журнал публикаций).
- `Restart=on-failure` перезапускает сервис при падении; плановые сбои
  источников (429 от arXiv) не роняют процесс — задачи логируют ошибку
  и ждут следующего цикла.
