FROM python:3.13-slim AS builder
RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc libc6-dev curl && \
    curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y && \
    rm -rf /var/lib/apt/lists/*
ENV PATH="/root/.cargo/bin:${PATH}"
WORKDIR /build
COPY pyproject.toml .
RUN mkdir -p dlsite_opds && touch dlsite_opds/__init__.py && \
    pip install --no-cache-dir . && \
    pip uninstall -y dlsite-opds
COPY dlsite_opds/ dlsite_opds/
RUN pip install --no-cache-dir --no-deps .

FROM python:3.13-slim
LABEL org.opencontainers.image.source="https://github.com/aokazenozomi/dlsite_opds" \
      org.opencontainers.image.description="OPDS 1.2 server for DLsite Play purchases" \
      org.opencontainers.image.licenses="MIT"
RUN useradd --create-home app
COPY --link --from=builder /usr/local /usr/local
USER app
ENV DLSITE_OPDS_HOST=0.0.0.0
ENV DLSITE_OPDS_PORT=2580
ENV DLSITE_OPDS_DATA_DIR=/data
EXPOSE 2580
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:2580/healthz')"]
ENTRYPOINT ["dlsite-opds"]
