"""HTTP API for creating and editing deterministic video timelines."""

from __future__ import annotations

from dataclasses import asdict
import hashlib
import os
import json
from typing import Any, Literal
from uuid import uuid4
from pathlib import Path

from fastapi import FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, Field
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.responses import JSONResponse

from .interpreter import Interpretation, apply_interpretation, interpret, resolve_clarification
from .renderer import RenderError, render as render_timeline
from .timeline import Timeline, TimelineError, Word

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

app = FastAPI(title="IncuBrix Prompt-Based Video Editing Engine", version="0.1.0")
_STATIC_DIR = Path(__file__).parent / "static"
app.mount("/assets", StaticFiles(directory=_STATIC_DIR), name="assets")
_sessions: dict[str, Timeline] = {}
_idempotency: dict[str, tuple[str, Any]] = {}
_pending_clarifications: dict[str, Interpretation] = {}
_source_files: dict[str, Path] = {}
_source_hashes: dict[str, str] = {}
_render_files: dict[str, Path] = {}


@app.get("/", include_in_schema=False)
def frontend() -> FileResponse:
    return FileResponse(_STATIC_DIR / "index.html")


@app.exception_handler(HTTPException)
async def structured_http_error(request: Request, exc: HTTPException) -> JSONResponse:
    if isinstance(exc.detail, dict) and "error" in exc.detail:
        payload = exc.detail
    else:
        payload = {"error": {"code": "HTTP_ERROR", "message": str(exc.detail), "retryable": False, "details": {}}}
    return JSONResponse(status_code=exc.status_code, content=payload, headers=exc.headers)


@app.exception_handler(RequestValidationError)
async def structured_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(status_code=422, content={"error": {
        "code": "REQUEST_VALIDATION", "message": "Request did not match the API schema",
        "retryable": False, "details": {"issues": jsonable_encoder(exc.errors())}
    }})


class WordInput(BaseModel):
    id: str
    text: str
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    speaker: str | None = None
    confidence: float = Field(default=1.0, ge=0, le=1)


class SessionInput(BaseModel):
    source_duration_s: float = Field(gt=0)
    words: list[WordInput] = Field(default_factory=list)

    def model_post_init(self, __context: Any) -> None:
        ids = [word.id for word in self.words]
        if len(ids) != len(set(ids)):
            raise ValueError("Transcript word ids must be unique")
        for word in self.words:
            if word.end <= word.start:
                raise ValueError(f"Word {word.id!r} must have end > start")
            if word.end > self.source_duration_s:
                raise ValueError(f"Word {word.id!r} ends after the source duration")


class EditInput(BaseModel):
    operation: Literal["cut", "keep", "speed", "reorder", "caption", "mute", "bleep", "update_caption", "restore", "restore_edit", "undo", "redo"]
    start_s: float | None = None
    end_s: float | None = None
    destination_s: float | None = None
    speed: float | None = None
    text: str | None = None
    word_ids: list[str] = Field(default_factory=list)
    annotation_id: str | None = None
    step: int | None = None
    style: dict[str, Any] = Field(default_factory=dict)


class InstructionInput(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/ready")
def ready() -> dict[str, str]:
    return {"status": "ready"}


@app.post("/api/v1/sessions", status_code=201)
def create_session(body: SessionInput, idempotency_key: str = Header(alias="Idempotency-Key")) -> dict[str, Any]:
    key = f"create:{idempotency_key}"
    digest = hashlib.sha256(body.model_dump_json().encode()).hexdigest()
    if key in _idempotency:
        old_digest, session_id = _idempotency[key]
        if old_digest != digest:
            raise HTTPException(409, detail={"error": {"code": "IDEMPOTENCY_CONFLICT", "message": "Key was used for a different request", "retryable": False, "details": {}}})
        return {"session_id": session_id, "timeline": _sessions[session_id].as_dict()}
    session_id = str(uuid4())
    words = [Word(**word.model_dump()) for word in body.words]
    _sessions[session_id] = Timeline(body.source_duration_s, words)
    _idempotency[key] = (digest, session_id)
    return {"session_id": session_id, "timeline": _sessions[session_id].as_dict()}


@app.post("/api/v1/sessions/from-upload", status_code=201)
def create_session_from_upload(
    video: UploadFile = File(...), source_duration_s: float = Form(...), words_json: str = Form("[]"),
    idempotency_key: str = Header(alias="Idempotency-Key")
) -> dict[str, Any]:
    if not video.content_type or not video.content_type.startswith("video/"):
        raise HTTPException(422, detail={"error": {"code": "INVALID_MEDIA", "message": "Upload a video file", "retryable": False, "details": {}}})
    try:
        raw_words = json.loads(words_json)
        if not isinstance(raw_words, list):
            raise ValueError("Transcript JSON must be a list of word objects")
        words = [WordInput(**word) for word in raw_words]
    except (ValueError, TypeError) as exc:
        raise HTTPException(422, detail={"error": {"code": "INVALID_SESSION", "message": str(exc), "retryable": False, "details": {}}}) from exc
    data_dir = Path(os.getenv("INCUBRIX_DATA_DIR", "./data"))
    originals = data_dir / "originals"
    originals.mkdir(parents=True, exist_ok=True)
    suffix = Path(video.filename or "source.mp4").suffix.lower()
    if suffix not in {".mp4", ".mov", ".webm", ".mkv", ".m4v"}:
        suffix = ".mp4"
    limit = int(os.getenv("INCUBRIX_MAX_VIDEO_BYTES", "524288000"))
    digest = hashlib.sha256()
    size = 0
    temp_path = originals / f".{uuid4()}.upload"
    try:
        with temp_path.open("wb") as target:
            while chunk := video.file.read(1024 * 1024):
                size += len(chunk)
                if size > limit:
                    raise HTTPException(413, detail={"error": {"code": "VIDEO_TOO_LARGE", "message": "Video exceeds the configured upload limit", "retryable": False, "details": {}}})
                digest.update(chunk)
                target.write(chunk)
        source_hash = digest.hexdigest()
        source = originals / f"{source_hash}{suffix}"
        if source.exists():
            temp_path.unlink(missing_ok=True)
        else:
            temp_path.replace(source)
        metadata = probe_media(source)
        try:
            # The probed media is authoritative; a stale form duration must not
            # create a 60-second timeline for a shorter uploaded video.
            body = SessionInput(source_duration_s=metadata["duration_s"], words=words)
        except ValueError as exc:
            raise HTTPException(422, detail={"error": {"code": "INVALID_TRANSCRIPT", "message": str(exc), "retryable": False, "details": metadata}}) from exc
        request_digest = hashlib.sha256((body.model_dump_json() + source_hash).encode()).hexdigest()
        key = f"create-upload:{idempotency_key}"
        if key in _idempotency:
            old_digest, session_id = _idempotency[key]
            if old_digest != request_digest:
                raise HTTPException(409, detail={"error": {"code": "IDEMPOTENCY_CONFLICT", "message": "Key was used for a different request", "retryable": False, "details": {}}})
            return {"session_id": session_id, "timeline": _sessions[session_id].as_dict(), "source_sha256": source_hash}
        session_id = str(uuid4())
        _sessions[session_id] = Timeline(body.source_duration_s, [Word(**w.model_dump()) for w in body.words])
        _source_files[session_id], _source_hashes[session_id] = source, source_hash
        _idempotency[key] = (request_digest, session_id)
        return {"session_id": session_id, "timeline": _sessions[session_id].as_dict(),
                "source_sha256": source_hash, "source_metadata": metadata}
    except RenderError as exc:
        raise HTTPException(422, detail={"error": {"code": "INVALID_MEDIA", "message": str(exc), "retryable": False, "details": {}}}) from exc
    finally:
        temp_path.unlink(missing_ok=True)
        video.file.close()


def probe_media(path: Path) -> dict[str, Any]:
    from .renderer import probe
    return probe(path)


@app.get("/api/v1/sessions/{session_id}")
def get_session(session_id: str) -> dict[str, Any]:
    timeline = _sessions.get(session_id)
    if timeline is None:
        raise HTTPException(404, detail={"error": {"code": "SESSION_NOT_FOUND", "message": "Session not found", "retryable": False, "details": {}}})
    return {"session_id": session_id, "timeline": timeline.as_dict(),
            "source_sha256": _source_hashes.get(session_id)}


@app.post("/api/v1/sessions/{session_id}/edits")
def apply_edit(session_id: str, body: EditInput, idempotency_key: str = Header(alias="Idempotency-Key")) -> dict[str, Any]:
    timeline = _sessions.get(session_id)
    if timeline is None:
        raise HTTPException(404, detail={"error": {"code": "SESSION_NOT_FOUND", "message": "Session not found", "retryable": False, "details": {}}})
    key = f"edit:{session_id}:{idempotency_key}"
    request_hash = hashlib.sha256((session_id + body.model_dump_json()).encode()).hexdigest()
    if key in _idempotency:
        old_digest, previous_result = _idempotency[key]
        if old_digest != request_hash:
            raise HTTPException(409, detail={"error": {"code": "IDEMPOTENCY_CONFLICT", "message": "Key was used for a different request", "retryable": False, "details": {}}})
        return {"session_id": session_id, "timeline": timeline.as_dict(), "replayed": True, "result": previous_result}
    try:
        result: Any
        if body.operation in {"cut", "keep", "speed", "restore", "reorder"}:
            if body.start_s is None or body.end_s is None:
                raise TimelineError("start_s and end_s are required for this operation")
            if body.operation == "cut": result = timeline.cut(body.start_s, body.end_s)
            elif body.operation == "keep": result = timeline.keep(body.start_s, body.end_s)
            elif body.operation == "restore": result = timeline.restore_source_range(body.start_s, body.end_s)
            elif body.operation == "reorder":
                if body.destination_s is None: raise TimelineError("destination_s is required")
                result = timeline.reorder(body.start_s, body.end_s, body.destination_s)
            else:
                if body.speed is None: raise TimelineError("speed is required")
                result = timeline.set_speed(body.start_s, body.end_s, body.speed)
        elif body.operation == "caption":
            if not body.text: raise TimelineError("Caption text is required")
            result = asdict(timeline.add_caption(body.text, body.word_ids, body.style))
        elif body.operation == "restore_edit":
            if body.step is None: raise TimelineError("step is required")
            result = timeline.restore_edit(body.step)
        elif body.operation in {"mute", "bleep"}:
            result = asdict(timeline.add_word_effect(body.operation, body.word_ids))
        elif body.operation == "update_caption":
            if not body.annotation_id: raise TimelineError("annotation_id is required")
            changes = {"style": body.style} if body.style else {}
            if body.text is not None: changes["text"] = body.text
            result = asdict(timeline.update_annotation(body.annotation_id, **changes))
        elif body.operation == "undo": result = {"reversed": timeline.undo()}
        else: result = {"restored": timeline.redo()}
    except TimelineError as exc:
        raise HTTPException(422, detail={"error": {"code": "EDIT_REJECTED", "message": str(exc), "retryable": False, "details": {}}}) from exc
    _idempotency[key] = (request_hash, result)
    return {"session_id": session_id, "status": "applied", "result": result, "timeline": timeline.as_dict(), "replayed": False}


@app.post("/api/v1/sessions/{session_id}/instructions")
def apply_instruction(session_id: str, body: InstructionInput, idempotency_key: str = Header(alias="Idempotency-Key")) -> dict[str, Any]:
    timeline = _sessions.get(session_id)
    if timeline is None:
        raise HTTPException(404, detail={"error": {"code": "SESSION_NOT_FOUND", "message": "Session not found", "retryable": False, "details": {}}})
    key = f"instruction:{session_id}:{idempotency_key}"
    request_hash = hashlib.sha256((session_id + body.model_dump_json()).encode()).hexdigest()
    if key in _idempotency:
        old_digest, result = _idempotency[key]
        if old_digest != request_hash:
            raise HTTPException(409, detail={"error": {"code": "IDEMPOTENCY_CONFLICT", "message": "Key was used for a different request", "retryable": False, "details": {}}})
        return {"session_id": session_id, **result, "timeline": timeline.as_dict(), "replayed": True}
    pending = _pending_clarifications.get(session_id)
    interpretation = resolve_clarification(pending, body.text) if pending else interpret(body.text, timeline)
    result = apply_interpretation(interpretation, timeline)
    if result["status"] == "needs_clarification" and interpretation.candidates:
        _pending_clarifications[session_id] = interpretation
    elif result["status"] == "applied":
        _pending_clarifications.pop(session_id, None)
    result["timeline"] = timeline.as_dict()
    result["replayed"] = False
    _idempotency[key] = (request_hash, result)
    return {"session_id": session_id, **result}


@app.post("/api/v1/sessions/{session_id}/render")
def render_session(session_id: str, idempotency_key: str = Header(alias="Idempotency-Key")) -> dict[str, Any]:
    if session_id not in _sessions:
        raise HTTPException(404, detail={"error": {"code": "SESSION_NOT_FOUND", "message": "Session not found", "retryable": False, "details": {}}})
    source = _source_files.get(session_id)
    if source is None:
        raise HTTPException(422, detail={"error": {"code": "SOURCE_REQUIRED", "message": "Upload a source video before rendering", "retryable": False, "details": {}}})
    request_hash = hashlib.sha256((session_id + _sessions[session_id].as_dict()["timeline_hash"]).encode()).hexdigest()
    key = f"render:{session_id}:{idempotency_key}"
    if key in _idempotency:
        old_hash, result = _idempotency[key]
        if old_hash != request_hash:
            raise HTTPException(409, detail={"error": {"code": "IDEMPOTENCY_CONFLICT", "message": "Key was used for a different timeline", "retryable": False, "details": {}}})
        return {**result, "replayed": True}
    output = Path(os.getenv("INCUBRIX_DATA_DIR", "./data")) / "outputs" / f"{session_id}.mp4"
    try:
        metadata = render_timeline(source, output, _sessions[session_id])
        if metadata["source_sha256"] != _source_hashes.get(session_id):
            output.unlink(missing_ok=True)
            raise RenderError("The stored original no longer matches its intake hash")
    except RenderError as exc:
        raise HTTPException(422, detail={"error": {"code": "RENDER_FAILED", "message": str(exc), "retryable": False, "details": {}}}) from exc
    _render_files[session_id] = output
    result = {"session_id": session_id, "status": "applied", "url": f"/api/v1/sessions/{session_id}/rendered",
              "timeline_hash": _sessions[session_id].as_dict()["timeline_hash"],
              "source_sha256": _source_hashes.get(session_id), "render": metadata, "replayed": False}
    _idempotency[key] = (request_hash, result)
    return result


@app.get("/api/v1/sessions/{session_id}/rendered")
def get_rendered_video(session_id: str) -> FileResponse:
    output = _render_files.get(session_id)
    if output is None or not output.is_file():
        raise HTTPException(404, detail={"error": {"code": "RENDER_NOT_FOUND", "message": "No rendered video exists for this session", "retryable": False, "details": {}}})
    return FileResponse(output, media_type="video/mp4", filename=f"incubrix-{session_id}.mp4")

