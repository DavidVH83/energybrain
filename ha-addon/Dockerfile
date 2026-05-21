FROM python:3.11-slim-bookworm

# Install build dependencies (Debian-based, niet Alpine)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    python3-dev \
    libffi-dev \
    libssl-dev \
    libopenblas-dev \
    gfortran \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml ./
COPY energybrain/ ./energybrain/

RUN pip install --no-cache-dir \
    "aiohttp>=3.9" \
    "python-dotenv>=1.0" \
    "pymodbus>=3.6" \
    "aiosqlite>=0.20" \
    "scikit-learn>=1.4" \
    "scipy>=1.12" \
    "numpy>=1.26" \
    "pandas>=2.2" \
    "joblib>=1.3" \
    "structlog>=24.0" \
    && pip install --no-cache-dir -e .

COPY ha-addon/run.sh /run.sh
RUN chmod +x /run.sh

CMD ["/run.sh"]
