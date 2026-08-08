# -*- coding: utf-8 -*-
"""The actual functions that read/write the 4 tables. Everything above
this layer (the FastAPI routes) should go through these, never touch
the tables directly. Each function is deliberately small and named after
one real step in the actual flow: upload -> detect -> translate -> view.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.models import Project, Segment, Translation, TranslatedSegment


def create_project(session: Session, filename: str, source_format: str, file_data: bytes) -> Project:
    project = Project(filename=filename, source_format=source_format, file_data=file_data)
    session.add(project)
    session.commit()
    session.refresh(project)
    return project


def save_segments(session: Session, project_id: uuid.UUID, parsed_segments: list[dict]) -> list[Segment]:
    """Takes exactly what a parser (word_parser/excel_parser/pdf_parser)
    already produces and saves it. Format-specific fields go into
    `structure` as-is, no reshaping needed."""
    known_fields = {"id", "type", "order", "text"}
    rows = []
    for seg in parsed_segments:
        structure = {k: v for k, v in seg.items() if k not in known_fields}
        rows.append(Segment(
            project_id=project_id,
            original_id=seg["id"],
            order=seg["order"],
            type=seg["type"],
            text=seg["text"],
            structure=structure,
        ))
    session.add_all(rows)
    session.commit()
    for row in rows:
        session.refresh(row)
    return rows


def set_project_language(session: Session, project_id: uuid.UUID, source_language: str, confidence: float) -> None:
    project = session.get(Project, project_id)
    project.source_language = source_language
    project.detection_confidence = confidence
    session.commit()


def set_project_status(session: Session, project_id: uuid.UUID, status: str) -> None:
    project = session.get(Project, project_id)
    project.status = status
    session.commit()


def create_translation_job(session: Session, project_id: uuid.UUID, target_language: str, use_glossary: bool = True) -> Translation:
    translation = Translation(project_id=project_id, target_language=target_language, use_glossary=use_glossary, status="pending")
    session.add(translation)
    session.commit()
    session.refresh(translation)
    return translation


def set_translation_status(session: Session, translation_id: uuid.UUID, status: str, error: str = None) -> None:
    translation = session.get(Translation, translation_id)
    translation.status = status
    if error:
        translation.error = error
    if status in ("done", "failed"):
        from datetime import datetime, timezone
        translation.completed_at = datetime.now(timezone.utc)
    session.commit()


def save_translated_segments(session: Session, translation_id: uuid.UUID, translated_results: list[dict]) -> list[TranslatedSegment]:
    """`translated_results` is exactly what translate_document() already
    returns for one language. Each item still carries the original
    segment's `id` (spread via **seg in translate_document), which is how
    we find the matching Segment row to link back to."""
    project_id = session.get(Translation, translation_id).project_id
    segments_by_original_id = {
        s.original_id: s.id
        for s in session.scalars(select(Segment).where(Segment.project_id == project_id))
    }

    rows = []
    for result in translated_results:
        segment_id = segments_by_original_id.get(result["id"])
        if segment_id is None:
            continue  # shouldn't happen, but never silently crash the whole save over one bad id
        rows.append(TranslatedSegment(
            translation_id=translation_id,
            segment_id=segment_id,
            translation_text=result.get("translation"),
            error=result.get("error"),
            terms=result.get("terms", []),
            suspicious=result.get("suspicious", False),
        ))
    session.add_all(rows)
    session.commit()
    for row in rows:
        session.refresh(row)
    return rows


def get_project(session: Session, project_id: uuid.UUID) -> Project | None:
    return session.get(Project, project_id)


def get_segments(session: Session, project_id: uuid.UUID) -> list[Segment]:
    return list(session.scalars(
        select(Segment).where(Segment.project_id == project_id).order_by(Segment.order)
    ))


def get_translation(session: Session, translation_id: uuid.UUID) -> Translation | None:
    return session.get(Translation, translation_id)


def get_translated_segments(session: Session, translation_id: uuid.UUID) -> list[TranslatedSegment]:
    return list(session.scalars(
        select(TranslatedSegment)
        .join(Segment)
        .where(TranslatedSegment.translation_id == translation_id)
        .order_by(Segment.order)
    ))


def update_translated_segment_text(
    session: Session, translated_segment_id: uuid.UUID, translation_text: str
) -> TranslatedSegment | None:
    """Spec section 6 (Manuelle Bearbeitung): a reviewer's hand-corrected
    text overwrites the AI's output in the same column the rest of the app
    already reads from. Not a parallel "edited version" column, so export
    and the review screen never need to know which source the text came
    from, just what it currently is."""
    row = session.get(TranslatedSegment, translated_segment_id)
    if row is None:
        return None
    row.translation_text = translation_text
    row.edited = True
    session.commit()
    session.refresh(row)
    return row
