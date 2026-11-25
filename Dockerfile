# syntax=docker/dockerfile:1
# Version-Aware RAG: API + UI image (RAG-M11/M12).
#
# Built and restricted data never enter the image (.dockerignore): build them on the host
# (`vrag corpus fetch/build`, `vrag index build`) and mount them at run time. `/search` and
# `/compare` need only `data/`. `/answer` also needs the restricted sources for its fail-closed
# privacy audit (in `data/`) and the shared Gemini quota ledger (`benchmark/drafts/`), so the
# container never starts a fresh ledger. The key is passed at run time only, never baked in:
#   docker build -t version-aware-rag .
#   docker run --rm -p 8000:8000 -v "$PWD/data:/app/data" \
#       -v "$PWD/benchmark/drafts:/app/benchmark/drafts" \
#       -v "$HOME/.cache/huggingface:/home/app/.cache/huggingface" \
#       -e VRAG_GEMINI_API_KEY version-aware-rag
# The Hugging Face cache mount reuses the host's downloaded models (bge-small, the cross-encoder,
# the ColBERT checkpoint); without it each container downloads them again at startup.

FROM node:24-slim AS frontend
WORKDIR /ui
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim AS app
COPY --from=ghcr.io/astral-sh/uv:0.13.9 /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONUNBUFFERED=1 \
    UV_HTTP_TIMEOUT=300 UV_HTTP_RETRIES=10 UV_CONCURRENT_DOWNLOADS=2
COPY pyproject.toml uv.lock README.md LICENSE THIRD_PARTY_NOTICES.md ./
# The cache mount keeps downloaded wheels between builds, so a dropped connection costs one retry.
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-dev --no-install-project
COPY src/ src/
COPY configs/ configs/
COPY benchmark/protocol/ benchmark/protocol/
COPY data/manifest/ data/manifest/
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-dev
COPY --from=frontend /ui/dist frontend/dist
RUN useradd --create-home app && mkdir -p /home/app/.cache/huggingface \
    && chown -R app /app /home/app/.cache
USER app
EXPOSE 8000
# Run the installed entry point directly: no uv (and no uv cache) is needed at run time.
ENV PATH="/app/.venv/bin:$PATH"
CMD ["vrag", "serve", "--host", "0.0.0.0", "--port", "8000"]
