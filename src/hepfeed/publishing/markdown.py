"""Convert a conservative markdown subset of model output to Telegram HTML.

Telegram renders formatting only when a message is sent with an explicit
parse_mode and valid entities: raw ``**bold**`` in a plain-text message stays
literal. Language models tend to emit markdown anyway, so the publishing
cycle converts the text here and sends it with ``parse_mode=HTML``.

Supported subset: ``**bold**``, ``*italic*``, ``code``, ``[text](url)`` links,
bare URLs and ``> `` quote lines grouped into an expandable blockquote.
Anything else is HTML-escaped and left as plain text, so unexpected markup
cannot break the send.
"""

from __future__ import annotations

import html
import re

_MD_LINK_RE = re.compile(r"\[([^\]\n]+)\]\((https?://[^)\s]+)\)")
_BOLD_RE = re.compile(r"\*\*([^*\n]+)\*\*")
_ITALIC_RE = re.compile(r"(?<!\*)\*([^*\n]+)\*(?!\*)")
_CODE_RE = re.compile(r"`([^`\n]+)`")
_BARE_URL_RE = re.compile(r'(?<!")(?<!>)https?://[^\s<]+')
_QUOTE_PREFIX = "&gt;"


def _inline(text: str) -> str:
    """Apply inline conversions to already-escaped text (no blockquotes)."""
    text = _MD_LINK_RE.sub(r'<a href="\2">\1</a>', text)
    text = _BOLD_RE.sub(r"<b>\1</b>", text)
    text = _ITALIC_RE.sub(r"<i>\1</i>", text)
    text = _CODE_RE.sub(r"<code>\1</code>", text)

    def autolink(match: re.Match[str]) -> str:
        url = match.group(0).rstrip(".,;:!?)")
        return f'<a href="{url}">{url}</a>'

    return _BARE_URL_RE.sub(autolink, text)


def to_telegram_html(text: str) -> str:
    """Convert model markdown to HTML for ``parse_mode="HTML"`` sending.

    Consecutive lines starting with ``> `` become one expandable blockquote
    (Telegram collapses it until the reader taps it).
    """
    escaped = html.escape(text, quote=False)
    pieces: list[str] = []
    block: list[str] = []

    def flush_block() -> None:
        nonlocal block
        if block:
            pieces.append(f"<blockquote expandable>{_inline(chr(10).join(block))}</blockquote>")
            block = []

    for line in escaped.split("\n"):
        stripped = line.strip()
        if stripped.startswith(_QUOTE_PREFIX):
            block.append(stripped.removeprefix(_QUOTE_PREFIX).strip())
            continue
        flush_block()
        pieces.append(_inline(line) if stripped else line)
    flush_block()
    return "\n".join(pieces)
