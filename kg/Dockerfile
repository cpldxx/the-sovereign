# Sovereign KG API. Build context: the repository root.
FROM python:3.12-slim
RUN pip install --no-cache-dir uv==0.11.13
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONUNBUFFERED=1
WORKDIR /app/kg
COPY kg/pyproject.toml kg/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY kg/ ./
EXPOSE 8080
CMD ["uv", "run", "--no-sync", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080", \
     "--proxy-headers", "--forwarded-allow-ips", "*"]
