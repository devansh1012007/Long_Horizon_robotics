# RecoverGraph engine service — production image.
# Build (from repo root):  docker build -f engine/Dockerfile -t rg-engine .
# Run:                     docker run -p 3031:3031 -v rg_data:/app/data rg-engine
#
# The image ships the frozen research core (recovergraph/) + the service
# layer (engine/). MuJoCo is included so backend="mujoco" jobs are available.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    ENGINE_HOST=0.0.0.0 \
    ENGINE_PORT=3031 \
    ENGINE_DATA_DIR=/app/data/engine

WORKDIR /app

COPY engine/requirements.txt engine/requirements.txt
RUN pip install -r engine/requirements.txt && pip install mujoco>=3.1

COPY recovergraph/ recovergraph/
COPY engine/ engine/

# Run as non-root; data volume must be writable by uid 1000.
RUN useradd -m -u 1000 rguser && mkdir -p /app/data && chown -R rguser /app
USER rguser

EXPOSE 3031

HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:3031/health',timeout=4)" || exit 1

CMD ["python", "engine/index.py"]
