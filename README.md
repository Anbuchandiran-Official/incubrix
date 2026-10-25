# IncuBrix Prompt-Based Video Editing Engine

An Assessment 2 prototype focused on deterministic, reversible edits over transcript-aligned timelines.

## Current scope

- Source-backed segments with a consistent **original source time** policy.
- Cut, keep, speed change, caption anchors, restore-source-range, undo and redo.
- Section reorder and transcript-word mute/bleep annotations.
- Conservative instruction parsing for explicit time cuts, exact transcript phrases, captions, mute/bleep, filler-word removal, undo, and redo; ambiguous phrase matches return options.
- Stable timeline hashes for repeatability checks.
- HTTP endpoints for sessions and structured edits, plus health/readiness.
- JSON session runner for repeatable edit sequences.
- Browser frontend for session setup, conversational edits, clarification choices, and timeline/history review.
- Local source-video preview with timed caption overlays.
- Immutable server-side source uploads named by SHA-256; rendering creates a separate output file.
- FFmpeg render path for cuts, speed changes, segment order, captions, mute, and bleep annotations, with output duration and stream-sync checks against one frame.

This is an assessment prototype. It does not transcribe videos, its natural-language grammar is intentionally narrow, and sessions are held in process memory. Restart persistence and assessment evidence remain to be completed. The renderer requires FFmpeg and FFprobe; Docker installs them for the container.

## Run locally

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
uvicorn incubrix_editor.api:app --reload
```

Open the browser editor at `http://localhost:8000/`. Interactive API docs are at `http://localhost:8000/docs`.

Choose a video before creating a session to upload an immutable source copy and enable **Render edit**. Rendering is a separate operation and the original upload remains unchanged. Sessions reset when the server restarts.

## Run the unit suite

```sh
python -m pip install -e '.[dev]'
python -m pytest -q tests
```

Or use the compose test profile:

```sh
docker compose --profile test run --rm tests
```

## Run with Docker

```sh
docker compose up --build
```

## Deploy on Render

The repository includes a Render Blueprint (`render.yaml`). To publish, push this project to a GitHub or GitLab repository, connect that repository in the Render Dashboard, and deploy the Blueprint. It builds the Docker image, installs FFmpeg, and binds Uvicorn to Render's assigned port. The free service uses an ephemeral filesystem and keeps sessions in memory, so uploaded files and sessions are temporary and reset on restarts. This checkout must be pushed to a Git remote before Render can build it.

## Example conversational session

The self-contained example is [examples/clarification_session.json](examples/clarification_session.json). It uses synthetic transcript data and demonstrates a caption anchor, an ambiguous phrase, a clarification answer, and undo. The recorded output is [examples/clarification_session_results.json](examples/clarification_session_results.json).

```sh
incubrix-session examples/clarification_session.json --output results.json
```

## API sketch

- `POST /api/v1/sessions` with `Idempotency-Key`
- `POST /api/v1/sessions/from-upload` with multipart video and transcript fields
- `GET /api/v1/sessions/{session_id}`
- `POST /api/v1/sessions/{session_id}/edits` with `Idempotency-Key`
- `POST /api/v1/sessions/{session_id}/instructions` with `Idempotency-Key`
- `POST /api/v1/sessions/{session_id}/render` with `Idempotency-Key`
- `GET /health`, `GET /ready`

Every edit uses original source time. A cut does not change the identity or time of surviving words; their current display time is derived from the active segments. Caption anchors therefore follow source words across cuts and speed changes. The instruction endpoint supports a narrow grammar for explicit time cuts, exact transcript phrases, caption overlays, mute/bleep, filler-word removal, undo, and redo. Repeated matches prompt a numbered choice, and ordinal answers such as “the second one” resolve against that pending question. Filler-word removal is recorded as one compound edit, so a single undo restores all removed ranges.

