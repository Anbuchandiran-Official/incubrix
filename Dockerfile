FROM python:3.12-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg fonts-dejavu-core && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir '.[dev]'
EXPOSE 8000
CMD ["sh", "-c", "uvicorn incubrix_editor.api:app --host 0.0.0.0 --port ${PORT:-8000}"]

