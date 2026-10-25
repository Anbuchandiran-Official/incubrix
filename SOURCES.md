# Sources and third-party services

## AI assistance and runtime models

OpenAI Codex provided development assistance; see `AI_USE.md`. The application does not call a generative AI, speech-to-text, or external media-rendering API at runtime. Instruction parsing is a deterministic ruleset.

## Libraries and system packages

The Python dependencies declared in `pyproject.toml` are FastAPI, Uvicorn, Pydantic, python-dotenv, python-multipart, and imageio-ffmpeg. The development extra includes pytest. Setuptools is used by the build configuration. The Docker image uses Python 3.12 slim and installs FFmpeg and DejaVu fonts for video rendering and caption text.

## Data and media

Examples and tests use synthetic transcript data. No sample video or real person's media is bundled in the repository. A user must provide the video and timestamped transcript for an editing session.

## External services

The hosted web service runs on Render. The current Blueprint selects the Free plan and uses ephemeral storage. The app does not call a third-party AI, image-generation, transcription, or media-rendering API.
