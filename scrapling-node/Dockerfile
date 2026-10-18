# sip01-scrapling-node — headless SIP-01 crawler (Scrapling engine)
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PLAYWRIGHT_BROWSERS_PATH=/opt/browsers

WORKDIR /app

# System deps for the browser tiers (chromium/patchright headless).
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 libcups2 \
      libdrm2 libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 \
      libxrandr2 libgbm1 libasound2 libpango-1.0-0 libcairo2 \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install -r requirements.txt \
 # browser download for DynamicFetcher/StealthyFetcher tiers
 && scrapling install || true

# Non-root runtime user; /data holds the sqlite queue, outbox and indexer key.
RUN useradd --create-home --uid 10001 node \
 && mkdir -p /data /opt/browsers \
 && chown -R node:node /data /opt/browsers /app
USER node

COPY sip01_node ./sip01_node
COPY config.example.toml ./config.toml

ENV SIP01_DATA_DIR=/data
VOLUME ["/data"]

# Self-test the protocol vectors, then crawl (seeds via config or --seed).
CMD ["python", "-m", "sip01_node", "--config", "config.toml"]
