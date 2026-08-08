# -*- coding: utf-8 -*-
"""Pydantic models: what the API's requests/responses look like.

Deliberately separate from db/models.py (SQLAlchemy). Those describe what's
stored in the database, these describe what crosses the network as JSON.
Easy to confuse since both are called "models," but they're different jobs.
For example, a response never includes file_data, since raw file bytes have
no business being serialized into a JSON response.
"""

import uuid
from datetime import datetime

from pydantic import BaseModel


class ProjectResponse(BaseModel):
    id: uuid.UUID
    filename: str
    source_format: str
    source_language: str | None
    detection_confidence: float | None
    status: str
    segment_count: int
    uploaded_at: datetime

    model_config = {"from_attributes": True}  # lets this be built directly from a SQLAlchemy Project object.


class TranslationCreateRequest(BaseModel):
    """The first request body schema, not just a response one. This is
    what a client must send as JSON to start a translation job.

    target_languages is a list, matching spec section 3's "select one or
    several target languages, translate simultaneously." translate_document()
    already supports multiple languages concurrently; this was previously
    restricted to a single language only at the API layer, not the AI core."""
    target_languages: list[str]
    use_glossary: bool = True


class TranslationResponse(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    target_language: str
    use_glossary: bool
    status: str
    error: str | None
    created_at: datetime
    completed_at: datetime | None

    model_config = {"from_attributes": True}


class TranslatedSegmentResponse(BaseModel):
    id: uuid.UUID
    segment_id: uuid.UUID
    source_text: str  # spec section 4: preview needs original + translation side by side, not translation alone
    translation_text: str | None
    error: str | None
    terms: list
    suspicious: bool
    edited: bool  # spec section 6: distinguishes a reviewer's hand-corrected text from the AI's original

    model_config = {"from_attributes": True}


class SegmentEditRequest(BaseModel):
    translation_text: str


class TranslationDetailResponse(TranslationResponse):
    """Extends TranslationResponse (real inheritance, not just similar
    shape) with the actual translated content. This is what the polling
    endpoint returns. `segments` is naturally empty while status is
    "pending"/"running", since save_translated_segments() only ever runs
    once, right before the job is marked done/failed."""
    segments: list[TranslatedSegmentResponse] = []


class QualityWarning(BaseModel):
    type: str
    message: str


class QualityCheckResponse(BaseModel):
    translation_id: uuid.UUID
    warning_count: int
    warnings: list[QualityWarning]
