FROM ghcr.io/astral-sh/uv@sha256:e5b65587bce7de595f299855d7385fe7fca39b8a74baa261ba1b7147afa78e58

ARG HATS_VERSION=0.2.0
ARG HATS_REVISION=unknown
LABEL org.opencontainers.image.title="HATS" \
      org.opencontainers.image.description="Homelab Agent Tooling & Skills" \
      org.opencontainers.image.source="https://github.com/X1pheR/homelab-agent-tooling-skills-mcp" \
      org.opencontainers.image.version="${HATS_VERSION}" \
      org.opencontainers.image.revision="${HATS_REVISION}"

WORKDIR /app
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY docs ./docs
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable

ENV PATH="/app/.venv/bin:${PATH}"
EXPOSE 8080
CMD ["hats-ui", "--host", "0.0.0.0", "--port", "8080"]
