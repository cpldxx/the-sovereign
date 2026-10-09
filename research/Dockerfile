# Sovereign Research (DeerFlow). Build context: the repository root.
FROM python:3.12-slim
RUN pip install --no-cache-dir uv==0.11.13
# DeerFlow isn't on PyPI: uv installs it from its git tag.
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONUNBUFFERED=1
WORKDIR /app/research
COPY research/pyproject.toml research/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY research/ ./
# DeerFlow writes its store under the working directory: the data volume.
WORKDIR /data
EXPOSE 8070
CMD ["uv", "run", "--project", "/app/research", "--no-sync", "uvicorn", "server:app", "--app-dir", "/app/research", \
     "--host", "0.0.0.0", "--port", "8070", "--proxy-headers", "--forwarded-allow-ips", "*"]
