FROM ghcr.io/astral-sh/uv:python3.14-bookworm-slim

WORKDIR /app

# Install Python dependencies first for better layer caching.
COPY pyproject.toml uv.lock ./

RUN uv sync \
    --frozen \
    --no-dev \
    --no-install-project

# Copy the application.
COPY . .

# Install the project itself.
RUN uv sync \
    --frozen \
    --no-dev

# Create a non-root user.
RUN useradd \
    --system \
    --create-home \
    --home-dir /app \
    exporter && \
    chown -R exporter:exporter /app

USER exporter

ENV PATH="/app/.venv/bin:$PATH"

CMD ["python", "-m", "app.main"]