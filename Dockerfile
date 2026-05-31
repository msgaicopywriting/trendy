# syntax=docker/dockerfile:1
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

WORKDIR /app

# Install dependencies first for better layer caching.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Copy the application and install the project itself.
COPY . .
RUN uv sync --frozen --no-dev

# SQLite DB + inbox dirs. Mount a persistent volume at /app/data in production
# so the database survives restarts/redeploys.
RUN mkdir -p data/ahrefs_inbox data/gsc_inbox data/clusters

EXPOSE 8501

# $PORT is injected by hosts like Render/Railway; defaults to 8501 for local runs.
CMD ["sh", "-c", "uv run streamlit run app/Home.py --server.port ${PORT:-8501} --server.address 0.0.0.0 --server.headless true"]
