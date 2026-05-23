FROM python:3.11-slim-bookworm

# Build deps for scikit-learn / scipy C extensions + jq for options.json
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    python3-dev \
    libffi-dev \
    libssl-dev \
    libopenblas-dev \
    gfortran \
    git \
    jq \
    && rm -rf /var/lib/apt/lists/*

# Install EnergyBrain — commit hash in RUN-regel zodat Docker cache altijd bust
# 02742df = fix: watchdog SoC alarm skip bij CT disconnected
RUN pip install --no-cache-dir \
    "git+https://github.com/DavidVH83/energybrain.git@02742df44078ab3498a1d289427dc4a72b61d788#egg=energybrain"

COPY run.sh /run.sh
RUN chmod +x /run.sh

CMD ["/run.sh"]
