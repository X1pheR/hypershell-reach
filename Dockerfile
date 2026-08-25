FROM ghcr.io/astral-sh/uv@sha256:e5b65587bce7de595f299855d7385fe7fca39b8a74baa261ba1b7147afa78e58

ARG REACH_VERSION=0.5.0
ARG REACH_REVISION=unknown
ARG REACH_CREATED=unknown
LABEL org.opencontainers.image.title="Hypershell Reach" \
      org.opencontainers.image.description="Hypershell Reach" \
      org.opencontainers.image.source="https://github.com/X1pheR/hypershell-reach" \
      org.opencontainers.image.url="https://github.com/X1pheR/hypershell-reach" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.created="${REACH_CREATED}" \
      org.opencontainers.image.version="${REACH_VERSION}" \
      org.opencontainers.image.revision="${REACH_REVISION}"

WORKDIR /app
RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends openssh-client \
    && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY docs ./docs
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable

ENV PATH="/app/.venv/bin:${PATH}"
EXPOSE 8080
CMD ["reach", "--host", "0.0.0.0", "--port", "8080"]
