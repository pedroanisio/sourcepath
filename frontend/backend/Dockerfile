FROM python:3.12-slim AS base

ARG WITH_SBERT=0

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt requirements-sbert.txt ./
RUN pip install -r requirements.txt && \
    if [ "$WITH_SBERT" = "1" ]; then pip install -r requirements-sbert.txt; fi

COPY app.py ./

ENV CBM_OUTPUT_DIR=/data \
    PORT=8765

EXPOSE 8765

HEALTHCHECK --interval=10s --timeout=3s --retries=6 \
    CMD python -c "import urllib.request,sys;urllib.request.urlopen('http://127.0.0.1:8765/api/healthz').read();" || exit 1

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8765"]
