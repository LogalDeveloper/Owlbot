FROM git.logal.dev/logaldeveloper/ci-base:latest AS build

ENV UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --no-dev --frozen --no-install-project
COPY . .
RUN uv sync --no-dev --frozen --no-editable

FROM archlinux:base

LABEL org.opencontainers.image.title="Owlbot" \
      org.opencontainers.image.description="A modular, event-driven chat bot for Owncast" \
      org.opencontainers.image.url="https://git.logal.dev/LogalDeveloper/Owlbot" \
      org.opencontainers.image.source="https://git.logal.dev/LogalDeveloper/Owlbot" \
      org.opencontainers.image.documentation="https://git.logal.dev/LogalDeveloper/Owlbot/wiki" \
      org.opencontainers.image.vendor="Logan Fick" \
      org.opencontainers.image.authors="Logan Fick" \
      org.opencontainers.image.licenses="Apache-2.0" \
      org.opencontainers.image.base.name="archlinux:base"

RUN echo 'Server = https://mirrors.logal.dev/archlinux/$repo/os/$arch' > /etc/pacman.d/mirrorlist \
    && pacman -Syu --noconfirm ffmpeg python \
    && yes | pacman -Scc
RUN useradd --uid 1000 --user-group --create-home owlbot

WORKDIR /app
COPY --from=build --chown=owlbot:owlbot /app/.venv /app/.venv
RUN mkdir -p data modules \
    && chown owlbot:owlbot /app /app/data /app/modules

ENV PATH="/app/.venv/bin:$PATH"
USER owlbot

VOLUME ["/app/data", "/app/modules"]
EXPOSE 8081

ENTRYPOINT ["owlbot"]
CMD ["--host", "0.0.0.0"]
