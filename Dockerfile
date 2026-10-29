FROM python:3.13-slim
COPY --from=ghcr.io/astral-sh/uv:0.11.32 /uv /usr/local/bin/uv
WORKDIR /app
# Dépendances d'abord (couche mise en cache tant que uv.lock ne change pas)
COPY pyproject.toml uv.lock ./
COPY src ./src
RUN uv sync --frozen --no-dev
# Pas de root dans le conteneur
RUN useradd --system --no-create-home mcp
USER mcp
ENV PORT=8000
EXPOSE 8000
CMD ["/app/.venv/bin/serveur-early-http"]
