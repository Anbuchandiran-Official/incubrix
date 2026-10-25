from uuid import uuid4

from fastapi import HTTPException
import pytest
from pydantic import ValidationError

from incubrix_editor.api import EditInput, InstructionInput, SessionInput, app, apply_edit, apply_instruction, create_session


def test_session_rejects_invalid_or_duplicate_transcript_words():
    with pytest.raises(ValidationError):
        SessionInput(source_duration_s=10, words=[
            {"id": "same", "text": "a", "start": 2, "end": 3},
            {"id": "same", "text": "b", "start": 4, "end": 5}
        ])
    with pytest.raises(ValidationError):
        SessionInput(source_duration_s=10, words=[{"id": "late", "text": "x", "start": 9, "end": 11}])


def test_openapi_has_expected_routes_and_idempotency_headers():
    schema = app.openapi()
    assert "/api/v1/sessions/{session_id}/instructions" in schema["paths"]
    edit = schema["paths"]["/api/v1/sessions/{session_id}/edits"]["post"]
    assert any(p["name"] == "Idempotency-Key" for p in edit["parameters"])


def test_instruction_endpoint_applies_user_clarification():
    session = create_session(SessionInput(source_duration_s=60, words=[
        {"id": "first", "text": "pricing", "start": 2, "end": 2.2},
        {"id": "second", "text": "pricing", "start": 20, "end": 20.3}
    ]), str(uuid4()))["session_id"]
    question = apply_instruction(session, InstructionInput(text="Remove the part about pricing"), str(uuid4()))
    answer = apply_instruction(session, InstructionInput(text="the second one"), str(uuid4()))
    assert question["status"] == "needs_clarification"
    assert answer["status"] == "applied"
    assert answer["timeline"]["duration_s"] == 59.7


def test_create_session_and_idempotent_replay():
    key = str(uuid4())
    body = SessionInput(source_duration_s=60, words=[{"id": "w1", "text": "hello", "start": 10, "end": 10.5}])
    first = create_session(body, key)
    second = create_session(body, key)
    assert second["session_id"] == first["session_id"]


def test_reusing_idempotency_key_with_different_edit_conflicts():
    session = create_session(SessionInput(source_duration_s=60), str(uuid4()))["session_id"]
    key = str(uuid4())
    a = apply_edit(session, EditInput(operation="cut", start_s=0, end_s=5), key)
    assert a["status"] == "applied"
    with pytest.raises(HTTPException) as exc:
        apply_edit(session, EditInput(operation="cut", start_s=5, end_s=10), key)
    assert exc.value.status_code == 409


def test_instruction_endpoint_clarifies_ambiguous_phrase():
    session = create_session(SessionInput(source_duration_s=60, words=[
        {"id": "a", "text": "pricing", "start": 2, "end": 2.2},
        {"id": "b", "text": "pricing", "start": 20, "end": 20.3}
    ]), str(uuid4()))["session_id"]
    response = apply_instruction(session, InstructionInput(text="Remove the part about pricing"), str(uuid4()))
    assert response["status"] == "needs_clarification"
    assert len(response["options"]) == 2
