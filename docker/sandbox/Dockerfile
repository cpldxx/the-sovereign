# Sandbox for live-data tools (sensors) written by the Coder Agent.
# Each run: a fresh container, read-only, non-root, no capabilities, small memory/CPU/process limits, and an
# egress guard (sitecustomize.py) that only lets code reach public internet addresses.
FROM python:3.12-slim
RUN pip install --no-cache-dir httpx==0.28.1
COPY sitecustomize.py /usr/local/lib/python3.12/site-packages/sitecustomize.py
COPY runner.py /sandbox/runner.py
USER 65534:65534
ENTRYPOINT ["python", "-I", "/sandbox/runner.py"]
