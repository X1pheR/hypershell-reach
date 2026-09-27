FROM ghcr.io/astral-sh/uv@sha256:e5b65587bce7de595f299855d7385fe7fca39b8a74baa261ba1b7147afa78e58 AS uv
FROM ubuntu:24.04@sha256:008173c23f95b170204355c12626cb5a965d779a7e1283b09e9cffbb1bf33ca3

COPY --from=uv /usr/local/bin/uv /usr/local/bin/uv

ARG REACH_VERSION=0.9.0
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
    && DEBIAN_FRONTEND=noninteractive apt-get upgrade -y \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends ca-certificates python3.12 python3.12-venv openssh-client \
    && dpkg --compare-versions "$(dpkg-query -W openssh-client | cut -f2)" ge 1:9.6p1-3ubuntu13.18 \
    && userdel ubuntu \
    && groupadd --gid 2000 reach \
    && useradd --uid 1000 --gid 2000 --create-home --home-dir /home/reach reach \
    && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY docs ./docs
COPY src ./src
RUN UV_PYTHON_DOWNLOADS=never uv sync --frozen --no-dev --no-editable --python /usr/bin/python3.12

ENV PATH="/app/.venv/bin:${PATH}"
EXPOSE 8080
USER 1000:2000
CMD ["reach", "--host", "0.0.0.0", "--port", "8080"]
