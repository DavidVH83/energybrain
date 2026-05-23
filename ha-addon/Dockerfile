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
# f3e67a2 = feat: dagplan tab met PV forecast, taken en uitvoeringslog
RUN pip install --no-cache-dir \
    "git+https://github.com/DavidVH83/energybrain.git@f3e67a285c94307f101378f2bdfbea7a678782c7#egg=energybrain"

COPY run.sh /run.sh
RUN chmod +x /run.sh

CMD ["/run.sh"]
