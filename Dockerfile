ARG PYTHON_VERSION=3.12

FROM python:${PYTHON_VERSION}-slim AS builder
ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1
WORKDIR /build
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:${PATH}"
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --upgrade pip && pip install ".[pdf]"

FROM python:${PYTHON_VERSION}-slim AS runtime
ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    CP_CACHE_PATH=/data/cache.sqlite
# WeasyPrint runtime deps (pango/cairo + a font for non-ASCII text in PDFs).
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        libpango-1.0-0 libpangoft2-1.0-0 libcairo2 libgdk-pixbuf-2.0-0 \
        shared-mime-info fonts-dejavu-core \
 && rm -rf /var/lib/apt/lists/*
RUN useradd --create-home --uid 1000 cp && mkdir -p /data && chown cp:cp /data
COPY --from=builder /opt/venv /opt/venv
USER cp
WORKDIR /home/cp
VOLUME ["/data"]
ENTRYPOINT ["cp-mcp"]
