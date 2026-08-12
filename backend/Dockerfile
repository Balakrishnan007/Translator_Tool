# Matches backend/.python-version; slim keeps the image small.
FROM python:3.11-slim

# uv binary copied from Astral's official image, the documented approach.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# Dependencies installed before source is copied, so unrelated code changes
# don't invalidate Docker's layer cache and force a reinstall.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project

COPY . .
RUN uv sync --frozen

EXPOSE 8000

# 0.0.0.0 required: 127.0.0.1 would be unreachable from outside the container.
CMD ["uv", "run", "uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
