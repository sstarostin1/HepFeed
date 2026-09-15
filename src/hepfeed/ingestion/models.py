"""Data models shared by ingestion components."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class PaperRecord(BaseModel):
    """Normalised metadata of a single publication.

    Kept source-agnostic so that future INSPIRE / CDS / RSS ingestors
    (docs/SOURCES.md) can feed the same downstream pipeline.
    """

    source: str = "arxiv"

    arxiv_id: str | None = None
    arxiv_version: str | None = None
    title: str
    abstract: str = ""
    authors: list[str] = Field(default_factory=list)

    primary_category: str | None = None
    categories: list[str] = Field(default_factory=list)

    doi: str | None = None
    published: datetime | None = None
    updated: datetime | None = None

    pdf_url: str | None = None
    abs_url: str | None = None
