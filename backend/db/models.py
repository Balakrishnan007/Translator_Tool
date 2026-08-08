# -*- coding: utf-8 -*-
"""SQLAlchemy models: the 4 tables that persist what the AI core already
proves works, so a translation survives after the process ends instead of
disappearing when the terminal closes.

Deliberately not here: glossary management, user accounts, project history
and versioning, search indexes. All correctly out of MVP scope (see the
section-by-section spec audit earlier in this project).
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import String, Text, Boolean, ForeignKey, DateTime, Integer, LargeBinary
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    filename: Mapped[str] = mapped_column(String, nullable=False)
    source_format: Mapped[str] = mapped_column(String, nullable=False)  # docx | xlsx | pdf
    source_language: Mapped[str | None] = mapped_column(String, nullable=True)
    detection_confidence: Mapped[float | None] = mapped_column(nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False, default="uploaded")
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    # Raw bytes of the original uploaded file. Without this, reopening a
    # past project only ever shows extracted text, never the real original
    # (formatting/images/fonts). Stored directly as a Postgres column since
    # we already run Postgres and don't need a separate file-storage service
    # at this scale. Production would move this to object storage (S3),
    # noted for later, not built now.
    file_data: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)

    segments: Mapped[list["Segment"]] = relationship(back_populates="project", cascade="all, delete-orphan")
    translations: Mapped[list["Translation"]] = relationship(back_populates="project", cascade="all, delete-orphan")


class Segment(Base):
    """One unit of translatable source text. Mirrors exactly what the
    parsers already produce (paragraph/table_cell/row/table_row/text_block).
    Format-specific fields (table_index, sheet, page, cells, ...) go in
    `structure` as JSONB rather than dedicated columns, since they genuinely
    differ per source format, and rigid columns would be mostly empty per row."""
    __tablename__ = "segments"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    original_id: Mapped[str] = mapped_column(String, nullable=False)  # the parser's own id, e.g. "t0-r1-c2"
    order: Mapped[int] = mapped_column(Integer, nullable=False)
    type: Mapped[str] = mapped_column(String, nullable=False)  # paragraph | table_cell | row | table_row | text_block
    text: Mapped[str] = mapped_column(Text, nullable=False)
    structure: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    project: Mapped["Project"] = relationship(back_populates="segments")


class Translation(Base):
    """One translation job for one target language: this is the row a
    polling endpoint reads to report status back to the client, since
    translating a full document is slow enough that it must run as a
    background job, not block the request."""
    __tablename__ = "translations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    target_language: Mapped[str] = mapped_column(String, nullable=False)
    use_glossary: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[str] = mapped_column(String, nullable=False, default="pending")  # pending|running|done|failed
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    project: Mapped["Project"] = relationship(back_populates="translations")
    translated_segments: Mapped[list["TranslatedSegment"]] = relationship(
        back_populates="translation", cascade="all, delete-orphan"
    )


class TranslatedSegment(Base):
    """The actual output of real API spend, the one table that matters
    most to persist, since losing it means paying to regenerate it."""
    __tablename__ = "translated_segments"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    translation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("translations.id", ondelete="CASCADE"))
    segment_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("segments.id", ondelete="CASCADE"))
    translation_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    terms: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)  # category/alternatives/description/glossary_entry per term
    # translator.py already sets this when a segment still looks broken
    # after every retry. It was already being computed and silently thrown
    # away. Without it, a shaky segment is indistinguishable from a
    # confident one once it's in the database.
    suspicious: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Spec section 6 (Manuelle Bearbeitung): a reviewer can correct a
    # segment by hand before approval. Same pattern as `suspicious` above:
    # a human-corrected segment needs to stay distinguishable from an
    # untouched AI one once it's in the database.
    edited: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    translation: Mapped["Translation"] = relationship(back_populates="translated_segments")
    segment: Mapped["Segment"] = relationship()
