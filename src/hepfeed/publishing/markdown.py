"""Convert a conservative markdown subset of model output to Telegram HTML.

Telegram renders formatting only when a message is sent with an explicit
parse_mode and valid entities: raw ``**bold**`` in a plain-text message stays
literal. Language models tend to emit markdown anyway, so the publishing
cycle converts the text here and sends it with ``parse_mode=HTML``.

Supported subset (documented for the model in the system prompt, rule 9):

==============  ===============================  ===============================
marker          result                           Telegram tag
==============  ===============================  ===============================
``**x**``       bold                             ``<b>``
``*x*``         italic                           ``<i>``
``__x__``       underline                        ``<u>``
``~~x~~``       strikethrough                    ``<s>``
```x```         inline monospace                 ``<code>``
fenced block    monospace block (language tag    ``<pre>``
```` ``` ````   is dropped)
``[t](url)``    hyperlink                        ``<a href>``
bare URL        hyperlink                        ``<a href>``
``> line``      consecutive lines collapse into  ``<blockquote expandable>``
                an expandable quote
``- line``      consecutive lines become a       ``<ul><li>``
                bulleted list
``||x||``       spoiler                          ``<tg-spoiler>``
==============  ===============================  ===============================

Anything else - including LaTeX - is HTML-escaped and left as plain text, so
unexpected markup cannot break the send (LaTeX cannot be rendered in Telegram
messages at all; the prompt forbids it in favour of plain unicode text).
"""

from __future__ import annotations

import html
import re

_MD_LINK_RE = re.compile(r"\[([^\]\n]+)\]\((https?://[^)\s]+)\)")
_BOLD_RE = re.compile(r"\*\*([^*\n]+)\*\*")
_UNDERLINE_RE = re.compile(r"__([^_\n]+)__")
_ITALIC_RE = re.compile(r"(?<!\*)\*([^*\n]+)\*(?!\*)")
_STRIKE_RE = re.compile(r"~~([^~\n]+)~~")
_SPOILER_RE = re.compile(r"\|\|([^|\n]+)\|\|")
_CODE_RE = re.compile(r"`([^`\n]+)`")
_BARE_URL_RE = re.compile(r'(?<!")(?<!>)https?://[^\s<]+')
_QUOTE_PREFIX = "&gt;"
_LIST_PREFIX = "- "
_FENCE = "```"


def _inline(text: str) -> str:
    """Apply inline conversions to already-escaped text (no block elements)."""
    text = _MD_LINK_RE.sub(r'<a href="\2">\1</a>', text)
    text = _BOLD_RE.sub(r"<b>\1</b>", text)
    text = _UNDERLINE_RE.sub(r"<u>\1</u>", text)
    text = _ITALIC_RE.sub(r"<i>\1</i>", text)
    text = _STRIKE_RE.sub(r"<s>\1</s>", text)
    text = _SPOILER_RE.sub(r"<tg-spoiler>\1</tg-spoiler>", text)
    text = _CODE_RE.sub(r"<code>\1</code>", text)

    def autolink(match: re.Match[str]) -> str:
        url = match.group(0).rstrip(".,;:!?)")
        return f'<a href="{url}">{url}</a>'

    return _BARE_URL_RE.sub(autolink, text)


def to_telegram_html(text: str) -> str:
    """Convert model markdown to HTML for ``parse_mode="HTML"`` sending.

    Consecutive ``> `` lines become one expandable blockquote (Telegram
    collapses it until the reader taps it); consecutive ``- `` lines become a
    bulleted list; fenced ``` blocks become ``<pre>`` monospace blocks.
    """
    escaped = html.escape(text, quote=False)
    pieces: list[str] = []
    quote: list[str] = []
    bullets: list[str] = []
    fence: list[str] | None = None

    def flush_quote() -> None:
        nonlocal quote
        if quote:
            pieces.append(f"<blockquote expandable>{_inline(chr(10).join(quote))}</blockquote>")
            quote = []

    def flush_bullets() -> None:
        nonlocal bullets
        if bullets:
            items = "".join(f"<li>{_inline(item)}</li>" for item in bullets)
            pieces.append(f"<ul>{items}</ul>")
            bullets = []

    for line in escaped.split("\n"):
        stripped = line.strip()
        if fence is not None:
            if stripped.startswith(_FENCE):
                pieces.append(f"<pre>{chr(10).join(fence)}</pre>")
                fence = None
            else:
                fence.append(line)
            continue
        if stripped.startswith(_FENCE):
            flush_quote()
            flush_bullets()
            fence = []  # the optional language tag on the opening fence is dropped
            continue
        if stripped.startswith(_QUOTE_PREFIX):
            flush_bullets()
            quote.append(stripped.removeprefix(_QUOTE_PREFIX).strip())
            continue
        if stripped.startswith(_LIST_PREFIX):
            flush_quote()
            bullets.append(stripped.removeprefix(_LIST_PREFIX).strip())
            continue
        flush_quote()
        flush_bullets()
        pieces.append(_inline(line) if stripped else line)

    flush_quote()
    flush_bullets()
    if fence is not None:  # unclosed fence: flush what we have
        pieces.append(f"<pre>{chr(10).join(fence)}</pre>")
    return "\n".join(pieces)
