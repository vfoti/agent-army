FROM python:3.12-slim

WORKDIR /app
RUN apt-get update \
    && apt-get install -y --no-install-recommends docker.io \
    && rm -rf /var/lib/apt/lists/*
# Default (anthropic) role runner only. To use AGENT_ARMY_ROLE_RUNNER=deepagents,
# set DEEPAGENTS=1 at build time to also install the LangChain dependency tree.
ARG DEEPAGENTS=0
RUN pip install --no-cache-dir anthropic \
    && if [ "$DEEPAGENTS" = "1" ]; then \
        pip install --no-cache-dir deepagents langchain-anthropic; \
    fi

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
# Role execution backend (default shown; "deepagents" requires --build-arg DEEPAGENTS=1):
#   AGENT_ARMY_ROLE_RUNNER=anthropic

CMD ["python", "-m", "harness.service"]
