"""Prompt builders for note generation (docs/CONCEPT.md, sections 5 and 10.2).

The system prompt is the product text in Russian on purpose: notes are
published in Russian and the language policy lives in AGENTS.md.

The operator can replace the system prompt at runtime (admin command
``/prompt_set``): the new text is stored in an override file next to the
database (``data/system_prompt.txt``, not in git) and takes precedence over
the built-in ``SYSTEM_PROMPT``. This survives redeploys, unlike editing the
installed source file, and ``/prompt_reset`` restores the built-in text.
"""

from __future__ import annotations

from pathlib import Path

from hepfeed.config import Settings
from hepfeed.ingestion.models import PaperRecord

SYSTEM_PROMPT = """\
Ты — редактор русскоязычного Telegram-канала для профессионалов в физике \
элементарных частиц (ФЭЧ) и физике ускорителей (ФУ). Пиши заметку о новой статье строго в формате:

<Русский перевод заголовка (оригинал в скобках)>

📄 arXiv:<id> · DOI:<doi, если известен>
🏷 Теги: #тег1 #тег2 #тег3

<3–5 абзацев: контекст (какая группа, в каком эксперименте) → что \
сделано (метод, ключевые числа с единицами и неопределённостями) → значение → ограничения>

🔗 Ссылки:
• arXiv: <полная ссылка на abs-страницу>

Правила:
1. Используй ТОЛЬКО факты из данных пользователя. Если данных не хватает — \
не выдумывай: лучше пропусти пункт или прямо напиши «Контекст не восстановлен».
2. Терминология профессиональная, без упрощений; русские термины, но \
международные аббревиатуры и названия экспериментов (PDF, QCD, BSM, ATLAS) \
оставляй как есть.
3. Числа — с единицами и неопределённостями, как в статье (например, \
m_H = 125.11 ± 0.11 GeV).
4. Без преувеличений: не «революция», а «уточняет», «исключает», \
«первый прямой предел» - лаконичный сдержанный академический стиль.
5. Объём основного текста 2500–3500 символов; вся заметка строго меньше 4096 символов - \
допустимы вариации в этих пределах, без лишнего сокращения.
6. Эмодзи только служебные: 📄 👥 🏷 🔗. Никаких других.
7. Теги: 3–7 штук, латиницей, без пробелов, каждый начинается с #.
8. Если текст статьи в данных отсутствует или пуст - выведи только шапку \
(заголовок, идентификаторы, теги) и одну строку «Контекст не восстановлен: \
текст статьи не предоставлен». Не рассуждай об этом и не привлекай внешние \
знания о статье.
9. Разметка допускается только такая: **жирный**, *курсив*, `код`, \
[текст](ссылка) и цитата - блок, который читатель может развернуть (например, \
блок «Ограничения»), оформляй строками, начинающимися с «> ». Никакой другой \
разметки и табличного синтаксиса.
"""


def system_prompt_path(settings: Settings) -> Path:
    """Override file location: next to the SQLite database (``data/`` dir)."""
    data = settings.ensure_data_dir()
    base = data if data.is_dir() else data.parent
    return base / "system_prompt.txt"


def load_system_prompt(settings: Settings) -> tuple[str, bool]:
    """Return (system prompt text, is_custom) for a generation cycle."""
    path = system_prompt_path(settings)
    if path.is_file():
        try:
            custom = path.read_text(encoding="utf-8").strip()
        except OSError:
            return SYSTEM_PROMPT, False
        if custom:
            return custom, True
    return SYSTEM_PROMPT, False


def prompt_history_path(settings: Settings) -> Path:
    """Backup of the previous prompt version (n-1), next to the current one."""
    base = settings.ensure_data_dir()
    base = base if base.is_dir() else base.parent
    return base / "system_prompt.prev.txt"


def save_system_prompt(settings: Settings, text: str) -> Path:
    """Persist the new prompt; the current version is backed up as n-1."""
    path = system_prompt_path(settings)
    history = prompt_history_path(settings)
    if path.is_file():
        history.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    path.write_text(text.strip() + "\n", encoding="utf-8")
    return path


def rollback_system_prompt(settings: Settings) -> str | None:
    """Restore the previous prompt version; return its text or None if absent.

    The built-in prompt is never restored automatically: it is just the
    initial version and the operator can paste it back manually if needed.
    """
    path = system_prompt_path(settings)
    history = prompt_history_path(settings)
    if not history.is_file():
        return None
    restored = history.read_text(encoding="utf-8").strip()
    if restored:
        path.write_text(restored + "\n", encoding="utf-8")
    else:
        # empty backup means the previous state was "no custom prompt":
        # dropping the current file brings the built-in prompt back
        path.unlink(missing_ok=True)
    history.unlink()
    return restored or None


def build_user_prompt(record: PaperRecord, full_text: str | None = None) -> str:
    """Render the article data the model is allowed to rely on.

    ``full_text`` replaces the abstract section when available.
    """
    authors = ", ".join(record.authors[:5]) or "неизвестны"
    if len(record.authors) > 5:
        authors += " и др."
    published = record.published.date().isoformat() if record.published else "неизвестно"
    abs_url = record.abs_url or (
        f"https://arxiv.org/abs/{record.arxiv_id}" if record.arxiv_id else "нет"
    )
    body = [
        "Статья:",
        f"Заголовок: {record.title}",
        f"Авторы: {authors}",
        f"arXiv ID: {record.arxiv_id or 'нет'} ({abs_url})",
        f"DOI: {record.doi or 'нет'}",
        f"Категории arXiv: {', '.join(record.categories) or 'нет'}",
        f"Первичная категория: {record.primary_category or 'нет'}",
        f"Опубликована: {published}",
        "",
    ]
    if full_text:
        body.extend(["Полный текст статьи:", full_text])
    else:
        body.extend(["Абстракт:", record.abstract or "(абстракт отсутствует)"])
    body.extend(["", "Напиши заметку по правилам системного сообщения."])
    return "\n".join(body)
