# Sovereign Hermes (Head Agent + voice). Build context: the repository root.
# On Linux, speech-to-text runs faster-whisper on the CPU (mlx-whisper is for Apple GPUs) and speech comes from
# Hermes' TTS providers (VOICE_TTS=hermes) — the browser's own voice is the fallback.
FROM python:3.12-slim
RUN pip install --no-cache-dir uv==0.11.13
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONUNBUFFERED=1
WORKDIR /app/hermes
COPY hermes/pyproject.toml hermes/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY hermes/ ./
EXPOSE 8090
CMD ["uv", "run", "--no-sync", "uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8090", \
     "--proxy-headers", "--forwarded-allow-ips", "*"]
