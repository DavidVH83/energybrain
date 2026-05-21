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
# 2572eb1 = fix: traceback logging in realtime_loop voor diagnose lege error
RUN pip install --no-cache-dir \
    "git+https://github.com/DavidVH83/energybrain.git@2572eb1c776464b797013beb50c76ce76fa03f16#egg=energybrain"

COPY run.sh /run.sh
RUN chmod +x /run.sh

CMD ["/run.sh"]
