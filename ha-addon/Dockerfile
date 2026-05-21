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
# 3813510 = fix: due_actions, validate_action, Watchdog constructor
RUN pip install --no-cache-dir \
    "git+https://github.com/DavidVH83/energybrain.git@38135101223418fb04f86a402611df5741d5cc39#egg=energybrain"

COPY run.sh /run.sh
RUN chmod +x /run.sh

CMD ["/run.sh"]
