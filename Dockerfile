FROM python:3.12-slim

WORKDIR /app
RUN apt-get update \
    && apt-get install -y --no-install-recommends docker.io \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir anthropic

COPY harness/ harness/
COPY agents/ agents/
COPY tasks/examples/ tasks/examples/

# D6: file-based ledger lives on this volume
VOLUME /app/tasks

# D5: secrets via env vars (pass at run time, never bake in):
#   ANTHROPIC_API_KEY, GITHUB_TOKEN, AGENT_ARMY_REPO
# Budget guards (defaults shown):
#   AGENT_ARMY_BUDGET_USD_PER_TASK=5.0
#   AGENT_ARMY_BUDGET_USD_PER_ROLE=2.0
#   AGENT_ARMY_BUDGET_TOKENS_PER_TASK=500000

CMD ["python", "-m", "harness.service"]
