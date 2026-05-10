ARG BUILD_FROM
FROM $BUILD_FROM

# Install build deps for numpy/scipy/scikit-learn C extensions
RUN apk add --no-cache \
    gcc \
    g++ \
    musl-dev \
    python3-dev \
    py3-pip \
    libffi-dev \
    openssl-dev \
    gfortran \
    openblas-dev

WORKDIR /app

# Copy the full source tree (build context = repo root, set in build.yaml)
COPY pyproject.toml ./
COPY energybrain/ ./energybrain/

# Install the package and its runtime dependencies
RUN pip3 install --no-cache-dir --break-system-packages \
    aiohttp>=3.9 \
    python-dotenv>=1.0 \
    pymodbus>=3.6 \
    aiosqlite>=0.20 \
    scikit-learn>=1.4 \
    scipy>=1.12 \
    numpy>=1.26 \
    pandas>=2.2 \
    joblib>=1.3 \
    structlog>=24.0 \
    && pip3 install --no-cache-dir --break-system-packages -e .

COPY energybrain-addon/energybrain/run.sh /run.sh
RUN chmod +x /run.sh

CMD ["/run.sh"]
