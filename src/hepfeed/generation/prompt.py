"""Prompt builders for note generation (docs/CONCEPT.md, sections 5 and 10.2).

The system prompt is the product text in Russian on purpose: notes are
published in Russian and the language policy lives in AGENTS.md.
"""

from __future__ import annotations

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
"""


def build_user_prompt(record: PaperRecord) -> str:
    """Render the article data the model is allowed to rely on."""
    authors = ", ".join(record.authors[:5]) or "неизвестны"
    if len(record.authors) > 5:
        authors += " и др."
    published = record.published.date().isoformat() if record.published else "неизвестно"
    abs_url = record.abs_url or (
        f"https://arxiv.org/abs/{record.arxiv_id}" if record.arxiv_id else "нет"
    )
    return "\n".join(
        [
            "Статья:",
            f"Заголовок: {record.title}",
            f"Авторы: {authors}",
            f"arXiv ID: {record.arxiv_id or 'нет'} ({abs_url})",
            f"DOI: {record.doi or 'нет'}",
            f"Категории arXiv: {', '.join(record.categories) or 'нет'}",
            f"Первичная категория: {record.primary_category or 'нет'}",
            f"Опубликована: {published}",
            "",
            "Абстракт:",
            record.abstract or "(абстракт отсутствует)",
            "",
            "Напиши заметку по правилам системного сообщения.",
        ]
    )
