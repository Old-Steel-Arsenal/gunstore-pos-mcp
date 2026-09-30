# Remote connector image (GUNSTORE_MCP_TRANSPORT=http). The local stdio server
# is not run from this image — it stays a uv tool on the operator's machine.
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.8 /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
# Dependencies first: a code-only change reuses this layer.
RUN uv sync --frozen --no-dev --no-install-project
COPY gunstore_mcp ./gunstore_mcp
RUN uv sync --frozen --no-dev && useradd --system --no-create-home mcp
USER mcp
ENV GUNSTORE_MCP_TRANSPORT=http PATH="/app/.venv/bin:$PATH"
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s CMD ["python", "-m", "gunstore_mcp.healthcheck"]
ENTRYPOINT ["gunstore-mcp"]
