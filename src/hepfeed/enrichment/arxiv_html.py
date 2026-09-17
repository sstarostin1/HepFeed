"""Full-text extraction from the arXiv native HTML rendering (docs/SOURCES.md, 2.1).

The HTML pages are LaTeXML-generated from the LaTeX sources: the text layer
is clean (no OCR involved). Extraction keeps paragraphs and drops page
furniture (banners, navigation, license blocks).
"""

from __future__ import annotations

import logging
from html.parser import HTMLParser

import httpx

logger = logging.getLogger(__name__)

MIN_FULL_TEXT_CHARS = 2000
"""Shorter extractions are treated as failures (layout change etc.)."""

BLOCK_TAGS = {"p", "h1", "h2", "h3", "h4", "li", "figcaption", "div"}

JUNK_MARKERS = (
    "Report",
    "GitHub",
    "Learn more",
    "Back to",
    "funders",
    "Accessibility",
    "Operational Status",
    "Subscribe",
    "Copyright",
    "Privacy",
    "Why HTML",
    "nonprofit",
    "Content selection",
    "Describe the issue",
    "Submit",
    "Bookmark",
    "BibTeX citation",
    "Cite as",
    "Submission history",
    "Export BibTeX",
    "Loading",
    "endorsers",
    "Recommended",
    "Mendeley",
    "Reddit",
    "Citeulike",
    "Contribute",
    "Dismiss",
    "cookie",
    "Terms of Use",
    "Follow arXiv",
    "Toggle",
    "Whitespace",
    "License: arXiv.org",
    "Have a free development",
    "Thank you for your",
)


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs) -> None:  # type: ignore[no-untyped-def]
        if tag in ("script", "style"):
            self._skip += 1
        if tag in BLOCK_TAGS:
            self.parts.append("\n\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style") and self._skip:
            self._skip -= 1

    def handle_data(self, data: str) -> None:
        if not self._skip and data.strip():
            self.parts.append(data)


def extract_text(html: str) -> str:
    """Extract the article text from an arXiv HTML page, dropping furniture."""
    parser = _TextExtractor()
    parser.feed(html)
    raw = "".join(parser.parts)
    paragraphs = [" ".join(p.split()) for p in raw.split("\n\n")]
    clean = [p for p in paragraphs if p and len(p) >= 40 and not any(m in p for m in JUNK_MARKERS)]
    cut = next((i for i, p in enumerate(clean) if p.startswith("License:")), len(clean))
    return "\n\n".join(clean[:cut])


async def fetch_full_text(
    arxiv_id: str,
    *,
    version: str | None = None,
    max_chars: int = 150_000,
    timeout_seconds: float = 60.0,
    client: httpx.AsyncClient | None = None,
) -> str | None:
    """Fetch the arXiv HTML rendering and extract its text.

    Returns None when the rendering is unavailable (404, layout change, too
    short) - callers fall back to the abstract. No retries on purpose: the
    call happens once per paper and the result is cached in the store.
    """
    ident = f"{arxiv_id}{version or ''}"
    urls = (
        f"https://arxiv.org/html/{ident}",
        f"https://arxiv.org/html/{arxiv_id}",
    )
    http = client or httpx.AsyncClient(timeout=timeout_seconds, follow_redirects=True)
    try:
        for url in urls:
            try:
                response = await http.get(url)
            except httpx.TransportError as exc:
                logger.warning("arXiv HTML fetch failed for %s: %s", url, exc)
                return None
            if response.status_code == 404:
                continue
            if response.status_code != 200:
                logger.warning(
                    "arXiv HTML fetch for %s returned HTTP %d", url, response.status_code
                )
                return None
            text = extract_text(response.text)
            if len(text) < MIN_FULL_TEXT_CHARS:
                logger.warning("arXiv HTML extraction for %s too short (%d chars)", url, len(text))
                return None
            return text[:max_chars]
    finally:
        if client is None:
            await http.aclose()
    logger.info("no HTML rendering available for %s", arxiv_id)
    return None
