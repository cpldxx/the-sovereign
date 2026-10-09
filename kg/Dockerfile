# Sovereign KG API. Build context: the repository root (it also needs docker/sandbox).
FROM python:3.12-slim
RUN pip install --no-cache-dir uv==0.11.13
# The docker CLI: the KG starts each sensor run as a sibling container through the mounted Docker socket.
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates \
 && curl -fsSL "https://download.docker.com/linux/static/stable/$(uname -m)/docker-27.5.1.tgz" | tar -xz -C /tmp \
 && mv /tmp/docker/docker /usr/local/bin/docker && rm -rf /tmp/docker \
 && apt-get purge -y curl && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONUNBUFFERED=1
WORKDIR /app/kg
COPY kg/pyproject.toml kg/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY kg/ ./
COPY docker/sandbox /app/docker/sandbox
EXPOSE 8080
CMD ["uv", "run", "--no-sync", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080", \
     "--proxy-headers", "--forwarded-allow-ips", "*"]
