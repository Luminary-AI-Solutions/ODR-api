# Use the official Python image as a base
FROM python:3.12-slim

# uv for reproducible installs from uv.lock
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# Install system dependencies required by Playwright browsers
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    wget \
    libnss3 \
    libatk-bridge2.0-0 \
    libgtk-3-0 \
    libdrm2 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxrandr2 \
    libgbm1 \
    libasound2 \
    libpangocairo-1.0-0 \
    libcups2 \
    fonts-liberation \
    libpango-1.0-0 \
    libatk1.0-0 \
    ca-certificates \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies first so this layer is cached independently of app code
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# Shared browser install path — default is ~/.cache/ms-playwright, which differs for root vs appuser
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

# Install browsers before copying app — layer cached when only app/ changes
RUN mkdir -p /ms-playwright \
 && uv run python -m playwright install --with-deps

# Copy application source
COPY app/ ./app/

ENV PATH="/app/.venv/bin:$PATH"
ENV PORT=8000 \
    HOME=/home/appuser

EXPOSE 8000

# Non-root user with a real home dir — crawl4ai writes under ~/.crawl4ai at import time
RUN useradd -m -d /home/appuser --shell /bin/false appuser \
 && chown -R appuser:appuser /app /home/appuser /ms-playwright
USER appuser

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
