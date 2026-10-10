# forge-sandbox: runtime image for agent tool calls. Agents usually run with
# --network none, so all tooling the demo repos need is baked in here.
FROM python:3.12-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends git ripgrep make curl bash ca-certificates nodejs npm procps \
 && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir pytest fastapi httpx uvicorn ruff pydantic

RUN useradd --create-home --shell /bin/bash agent \
 && mkdir -p /workspace && chown agent:agent /workspace

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
USER agent
WORKDIR /workspace
RUN git config --global init.defaultBranch main && git config --global --add safe.directory /workspace
CMD ["sleep", "infinity"]
