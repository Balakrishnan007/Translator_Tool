# -*- coding: utf-8 -*-
"""The FastAPI app. One endpoint at a time, each tested before the next.

Run with: uv run uvicorn api.main:app --reload
"""

import os
import tempfile
import uuid

from fastapi import FastAPI, UploadFile, File, Depends, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, FileResponse
from starlette.background import BackgroundTask
from sqlalchemy.orm import Session

from db.session import get_session, SessionLocal
from db import crud
from parsers.upload_validator import validate_upload
from parsers.document_exporter import export_document, EXPORT_COMPATIBILITY
from ai.language_detector import detect_language_from_segments
from ai.tracing import flush_tracing
from ai.glossary_loader import load_glossary
from ai.translator import translate_document
from ai.quality_check import run_quality_check
from api.schemas import ProjectResponse, TranslationCreateRequest, TranslationResponse, TranslationDetailResponse, TranslatedSegmentResponse, QualityCheckResponse, SegmentResponse, SegmentEditRequest

app = FastAPI(title="Rotpunkt Translator API")

# The frontend is served from a separate origin (static file server on a
# different port), so the browser treats every API call as cross-origin.
# Without this, fetch() calls from the frontend are blocked by the browser
# before they even reach an endpoint.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5500", "http://127.0.0.1:5500"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _segment_to_dict(segment) -> dict:
    """Reverses save_segments()'s shape. Rebuilds the exact dict shape the
    parsers originally produced (id/type/order/text plus whatever
    format-specific fields were stashed in `structure`), since that's what
    translate_document() expects as input. Without this, the AI core would
    need to know about database rows, which it never should."""
    return {"id": segment.original_id, "type": segment.type, "order": segment.order, "text": segment.text, **segment.structure}


def _translated_segment_to_dict(translated_segment) -> dict:
    """Same idea as _segment_to_dict, but also carries the translation
    result fields (source_text/translation/terms), the shape both
    run_quality_check() and export_document() expect, since that's exactly
    what translate_document() itself already produces. Reuses the ORM
    relationship (translated_segment.segment) instead of a second query."""
    seg = translated_segment.segment
    return {
        **_segment_to_dict(seg),
        "source_text": seg.text,
        "translation": translated_segment.translation_text,
        # Same defensive coercion as read_translation()'s response model.
        # This is the other consumer of stored `terms` (quality-check and
        # export both go through this function), and it turns out it needed
        # the same guard. Confirmed real: run_quality_check() 500'd on the
        # 2 historically-corrupted rows because iterating a raw string
        # char-by-char as if it were a list of term dicts blows up on the
        # first `t["term"]` access.
        "terms": translated_segment.terms if isinstance(translated_segment.terms, list) else [],
        "error": translated_segment.error,
    }


def _project_to_response(project, segment_count: int, source_language, detection_confidence) -> ProjectResponse:
    """Shared by all 3 project-reading endpoints. segment_count and the
    language-detection fields are passed in explicitly rather than always
    read off `project`, since callers get them differently: create_project
    already has both from the just-run validation/detection (no need for a
    second DB query), while list/read_project pull them via a count query
    and the stored columns."""
    return ProjectResponse(
        id=project.id,
        filename=project.filename,
        source_format=project.source_format,
        source_language=source_language,
        detection_confidence=detection_confidence,
        status=project.status,
        segment_count=segment_count,
        uploaded_at=project.uploaded_at,
    )


def _translated_segment_to_response(row) -> TranslatedSegmentResponse:
    """Shared by read_translation and edit_translated_segment. Both build
    the exact same response shape from a TranslatedSegment row, including
    the same defensive `terms` coercion (see _translated_segment_to_dict's
    docstring for why that guard exists)."""
    return TranslatedSegmentResponse(
        id=row.id,
        segment_id=row.segment_id,
        source_text=row.segment.text,
        translation_text=row.translation_text,
        error=row.error,
        terms=row.terms if isinstance(row.terms, list) else [],
        suspicious=row.suspicious,
        edited=row.edited,
    )


def _run_translation_jobs(translation_ids_by_language: dict[str, uuid.UUID], project_id: uuid.UUID, use_glossary: bool):
    """Runs in the background, after the response has already been sent.
    Must open its own database session, since the request's session is
    closed by the time this runs (get_session()'s cleanup already fired).

    Handles all requested languages in one call to translate_document(),
    which already runs every (segment, language) pair concurrently via its
    own thread pool. This is what actually satisfies spec section 3's
    "translate several target languages simultaneously," not just looping
    over languages one at a time and calling it simultaneous."""
    session = SessionLocal()
    target_languages = list(translation_ids_by_language.keys())
    try:
        for tid in translation_ids_by_language.values():
            crud.set_translation_status(session, tid, "running")

        project = crud.get_project(session, project_id)
        segments = crud.get_segments(session, project_id)
        parsed_segments = [_segment_to_dict(s) for s in segments]

        glossary = load_glossary()
        results = translate_document(parsed_segments, project.source_language, target_languages, glossary, use_glossary)

        for language, translation_id in translation_ids_by_language.items():
            segment_results = results[language]
            crud.save_translated_segments(session, translation_id, segment_results)

            # translate_document() catches failures per segment on purpose, so
            # one bad segment never kills the whole batch. But that also means
            # a systemic failure (e.g. an invalid target_language) never raises
            # here either; every segment just silently has an "error" instead
            # of a translation. Confirmed real: this previously marked a job
            # "done" even though all 6 segments had failed. If literally
            # nothing succeeded, that's a job failure, not a completion.
            # Checked per language, since one language failing (e.g. a bad
            # language name) shouldn't mark every other language's job as
            # failed too.
            failures = sum(1 for r in segment_results if r.get("error"))
            if failures == len(segment_results) and segment_results:
                first_error = segment_results[0]["error"]
                crud.set_translation_status(session, translation_id, "failed", error=f"All {failures} segments failed: {first_error}")
            else:
                crud.set_translation_status(session, translation_id, "done")
    except Exception as e:
        # A failure here is systemic (e.g. couldn't load the glossary at
        # all), affecting every language's job, not just one.
        for tid in translation_ids_by_language.values():
            crud.set_translation_status(session, tid, "failed", error=str(e))
    finally:
        session.close()
        # Confirmed real gap, not hypothetical: flush_tracing() was only ever
        # called from standalone test scripts, never from the live server.
        # Every real translation made through the running app all session
        # left its spans sitting in memory, unsent. uvicorn --reload restarts
        # the whole process on every code edit, which wipes anything not yet
        # flushed. This background job is the right place: it runs after the
        # HTTP response is already sent, so flushing here costs the user
        # nothing in perceived latency.
        flush_tracing()


@app.post("/projects", response_model=ProjectResponse, status_code=201)
async def create_project(file: UploadFile = File(...), session: Session = Depends(get_session)):
    file_bytes = await file.read()
    ext = os.path.splitext(file.filename)[1].lower()

    # validate_upload needs a real file path on disk. Write to a temp file
    # and reuse the already-proven validator unchanged, instead of adapting
    # tested code to a new calling convention.
    with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
        tmp.write(file_bytes)
        tmp_path = tmp.name

    try:
        validation = validate_upload(tmp_path)
        if not validation["valid"]:
            raise HTTPException(status_code=400, detail=validation["error"])

        detection = detect_language_from_segments(validation["segments"])

        project = crud.create_project(
            session,
            filename=file.filename,
            source_format=ext.lstrip("."),
            file_data=file_bytes,
        )
        crud.save_segments(session, project.id, validation["segments"])
        crud.set_project_language(session, project.id, detection["language"], detection["confidence"])

        return _project_to_response(project, validation["segment_count"], detection["language"], detection["confidence"])
    finally:
        os.unlink(tmp_path)


@app.get("/projects", response_model=list[ProjectResponse])
def list_projects(session: Session = Depends(get_session)):
    projects = crud.list_projects(session)
    return [
        _project_to_response(p, crud.count_segments(session, p.id), p.source_language, p.detection_confidence)
        for p in projects
    ]


@app.get("/projects/{project_id}", response_model=ProjectResponse)
def read_project(project_id: uuid.UUID, session: Session = Depends(get_session)):
    project = crud.get_project(session, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")

    return _project_to_response(project, crud.count_segments(session, project_id), project.source_language, project.detection_confidence)


@app.get("/projects/{project_id}/segments", response_model=list[SegmentResponse])
def read_segments(project_id: uuid.UUID, session: Session = Depends(get_session)):
    project = crud.get_project(session, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")

    segments = crud.get_segments(session, project_id)
    return [SegmentResponse.model_validate(s) for s in segments]


@app.post("/projects/{project_id}/translations", response_model=list[TranslationResponse], status_code=202)
def create_translation(
    project_id: uuid.UUID,
    body: TranslationCreateRequest,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
):
    project = crud.get_project(session, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    if not project.source_language:
        raise HTTPException(status_code=400, detail="Project has no detected source language yet")
    if not body.target_languages:
        raise HTTPException(status_code=400, detail="target_languages must contain at least one language")

    # One Translation row per language (so each still has its own id to
    # poll individually), but all of them run together in a single
    # background call. That single call is what makes them genuinely
    # concurrent, not the number of database rows.
    translations = [
        crud.create_translation_job(session, project_id, lang, body.use_glossary)
        for lang in body.target_languages
    ]
    translation_ids_by_language = {t.target_language: t.id for t in translations}
    background_tasks.add_task(_run_translation_jobs, translation_ids_by_language, project_id, body.use_glossary)

    return [TranslationResponse.model_validate(t) for t in translations]


@app.get("/projects/{project_id}/translations/{translation_id}", response_model=TranslationDetailResponse)
def read_translation(project_id: uuid.UUID, translation_id: uuid.UUID, session: Session = Depends(get_session)):
    translation = crud.get_translation(session, translation_id)
    # Checking translation.project_id == project_id, not just that the
    # translation exists at all. Otherwise someone could fetch a real
    # translation ID through the wrong project's URL and it would still work.
    if translation is None or translation.project_id != project_id:
        raise HTTPException(status_code=404, detail="Translation not found")

    segments = crud.get_translated_segments(session, translation_id)

    return TranslationDetailResponse(
        id=translation.id,
        project_id=translation.project_id,
        target_language=translation.target_language,
        use_glossary=translation.use_glossary,
        status=translation.status,
        error=translation.error,
        created_at=translation.created_at,
        completed_at=translation.completed_at,
        segments=[_translated_segment_to_response(s) for s in segments],
    )


@app.patch(
    "/projects/{project_id}/translations/{translation_id}/segments/{translated_segment_id}",
    response_model=TranslatedSegmentResponse,
)
def edit_translated_segment(
    project_id: uuid.UUID,
    translation_id: uuid.UUID,
    translated_segment_id: uuid.UUID,
    body: SegmentEditRequest,
    session: Session = Depends(get_session),
):
    """Spec section 6 (Manuelle Bearbeitung): a reviewer can hand-correct a
    segment's translation before approving it. Restricted to status "done":
    editing is what happens between translate and approve, not before
    (nothing to edit yet) or after (an approved translation is exported
    as-is; re-opening it for edits would need a whole re-approval flow,
    correctly out of MVP scope)."""
    translation = crud.get_translation(session, translation_id)
    if translation is None or translation.project_id != project_id:
        raise HTTPException(status_code=404, detail="Translation not found")
    if translation.status != "done":
        raise HTTPException(
            status_code=409,
            detail=f"Translation is '{translation.status}': segments can only be edited while status is 'done'",
        )

    segments = crud.get_translated_segments(session, translation_id)
    if not any(s.id == translated_segment_id for s in segments):
        raise HTTPException(status_code=404, detail="Segment not found in this translation")

    updated = crud.update_translated_segment_text(session, translated_segment_id, body.translation_text)
    return _translated_segment_to_response(updated)


def _get_owned_translation(session: Session, project_id: uuid.UUID, translation_id: uuid.UUID):
    """Shared by quality-check and export. Both need the same ownership
    check endpoint #4 already established (translation must actually
    belong to the project in the URL), plus the project itself for its
    source_language/source_format/filename."""
    project = crud.get_project(session, project_id)
    translation = crud.get_translation(session, translation_id)
    if project is None or translation is None or translation.project_id != project_id:
        raise HTTPException(status_code=404, detail="Translation not found")
    # "approved" included alongside "done": quality-check must stay
    # readable after approval too (a reviewer revisiting an approved job),
    # not just in the done-but-not-yet-approved window.
    if translation.status not in ("done", "failed", "approved"):
        raise HTTPException(status_code=409, detail=f"Translation is still '{translation.status}', not ready yet")
    return project, translation


@app.post("/projects/{project_id}/translations/{translation_id}/approve", response_model=TranslationResponse)
def approve_translation(project_id: uuid.UUID, translation_id: uuid.UUID, session: Session = Depends(get_session)):
    """Spec section 9: only after this explicit approval does export unlock,
    matching the workflow diagram's Qualitätsprüfung -> Freigabe -> Export
    order. We don't regenerate anything on approval (the reviewed segments
    are the final ones already); this is a human sign-off gate, not a
    second translation pass."""
    translation = crud.get_translation(session, translation_id)
    if translation is None or translation.project_id != project_id:
        raise HTTPException(status_code=404, detail="Translation not found")
    if translation.status != "done":
        raise HTTPException(
            status_code=409,
            detail=f"Translation is '{translation.status}': only a completed translation can be approved",
        )
    crud.set_translation_status(session, translation_id, "approved")
    session.refresh(translation)
    return TranslationResponse.model_validate(translation)


@app.get("/projects/{project_id}/translations/{translation_id}/quality-check", response_model=QualityCheckResponse)
def read_quality_check(project_id: uuid.UUID, translation_id: uuid.UUID, session: Session = Depends(get_session)):
    project, translation = _get_owned_translation(session, project_id, translation_id)

    translated_segments = crud.get_translated_segments(session, translation_id)
    segments = [_translated_segment_to_dict(ts) for ts in translated_segments]

    glossary = load_glossary()
    warnings = run_quality_check(segments, glossary, project.source_language)

    return QualityCheckResponse(
        translation_id=translation_id,
        warning_count=len(warnings),
        warnings=warnings,
    )


def _make_bilingual_segments(segments: list[dict]) -> list[dict]:
    """Spec option "Original + Übersetzung": combines source and translation
    into one text per segment, then reuses the existing writers unchanged.
    True side-by-side columns would need new layout code in every writer;
    this achieves the spec's actual requirement (both texts present) with
    data we already have, not a bigger redesign."""
    combined = []
    for seg in segments:
        new_seg = dict(seg)
        source = seg.get("source_text") or ""
        translation = seg.get("translation") or ""
        new_seg["translation"] = f"Original: {source}\nTranslation: {translation}"
        combined.append(new_seg)
    return combined


def _build_quality_report_segments(warnings: list[dict], target_language: str) -> list[dict]:
    """Spec option "Prüfbericht": turns quality-check warnings into plain
    paragraph segments, so the existing paragraph-writing path in every
    writer can export it with no new writer code at all."""
    segments = [{
        "id": "report-header", "type": "paragraph", "order": 0,
        "translation": f"Quality report ({target_language}): {len(warnings)} warning(s)",
    }]
    for i, w in enumerate(warnings, start=1):
        segments.append({
            "id": f"report-{i}", "type": "paragraph", "order": i,
            "translation": f"[{w['type']}] {w['message']}",
        })
    return segments


@app.get("/projects/{project_id}/translations/{translation_id}/export")
def export_translation(
    project_id: uuid.UUID,
    translation_id: uuid.UUID,
    format: str,
    mode: str = "translation_only",
    session: Session = Depends(get_session),
):
    project, translation = _get_owned_translation(session, project_id, translation_id)
    if translation.status != "approved":
        raise HTTPException(
            status_code=409,
            detail="Translation must be approved before it can be exported (spec section 9: Freigabe precedes Export)",
        )

    allowed = EXPORT_COMPATIBILITY.get(project.source_format, set())
    if format not in allowed:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot export a {project.source_format} document as {format}. Allowed: {', '.join(sorted(allowed))}",
        )
    if mode not in ("translation_only", "bilingual", "quality_report"):
        raise HTTPException(status_code=400, detail="mode must be one of: translation_only, bilingual, quality_report")

    translated_segments = crud.get_translated_segments(session, translation_id)
    segments = [_translated_segment_to_dict(ts) for ts in translated_segments]

    if mode == "bilingual":
        segments = _make_bilingual_segments(segments)
    elif mode == "quality_report":
        glossary = load_glossary()
        warnings = run_quality_check(segments, glossary, project.source_language)
        segments = _build_quality_report_segments(warnings, translation.target_language)

    # NamedTemporaryFile, not tempfile.mktemp(). mktemp() only returns a
    # name, it never reserves it, leaving a gap where something else could
    # claim the same path first (Python's own docs call this unsafe). This
    # actually creates and reserves the file in one atomic step, same
    # pattern already used for the upload endpoint's temp file above.
    with tempfile.NamedTemporaryFile(suffix=f".{format}", delete=False) as tmp:
        out_path = tmp.name
    export_document(segments, project.source_format, format, out_path, language=translation.target_language)

    base_name = os.path.splitext(project.filename)[0]
    mode_suffix = "" if mode == "translation_only" else f"_{mode}"
    download_name = f"{base_name}_{translation.target_language}{mode_suffix}.{format}"

    # BackgroundTask here runs after the file has been streamed to the
    # client, same "cleanup after the response, not before" pattern as
    # the POST /projects temp file, just via FastAPI's response-level hook
    # instead of a try/finally, since FileResponse streams asynchronously.
    return FileResponse(
        out_path,
        filename=download_name,
        background=BackgroundTask(os.unlink, out_path),
    )


@app.get("/projects/{project_id}/file")
def download_original_file(project_id: uuid.UUID, session: Session = Depends(get_session)):
    project = crud.get_project(session, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    if not project.file_data:
        raise HTTPException(status_code=404, detail="Original file was not stored for this project")

    return Response(
        content=project.file_data,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{project.filename}"'},
    )
