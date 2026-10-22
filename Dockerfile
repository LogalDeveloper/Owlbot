# syntax=docker/dockerfile:1.19

# Build stage
FROM python:3.14-slim-trixie AS build

COPY --from=ghcr.io/astral-sh/uv:0.13.4 /uv /usr/local/bin/uv

RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

ENV UV_LINK_MODE=copy \
    UV_NO_CACHE=1 \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv

# Install dependencies and application
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --no-dev --locked --no-install-project
COPY --exclude=.git . .
RUN --mount=type=bind,source=.git,target=/app/.git \
    uv sync --no-dev --locked --no-editable

# Runtime stage
FROM python:3.14-slim-trixie AS runtime

LABEL org.opencontainers.image.title="Owlbot" \
    org.opencontainers.image.description="A modular, event-driven chat bot for Owncast" \
    org.opencontainers.image.url="https://github.com/LogalDeveloper/Owlbot" \
    org.opencontainers.image.source="https://github.com/LogalDeveloper/Owlbot" \
    org.opencontainers.image.documentation="https://github.com/LogalDeveloper/Owlbot/wiki" \
    org.opencontainers.image.vendor="Logan Fick" \
    org.opencontainers.image.authors="Logan Fick" \
    org.opencontainers.image.licenses="Apache-2.0" \
    org.opencontainers.image.base.name="python:3.14-slim-trixie"

# Runtime dependencies and user
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*
RUN useradd --uid 1000 --user-group --create-home owlbot

# Application files and permissions
WORKDIR /app
COPY --from=build --chown=owlbot:owlbot /app/.venv /app/.venv
RUN mkdir -p data modules \
    && chown owlbot:owlbot /app /app/data /app/modules

# Runtime configuration
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1
USER owlbot

VOLUME ["/app/data", "/app/modules"]
EXPOSE 8081

ENTRYPOINT ["owlbot"]
CMD ["--host", "0.0.0.0"]
