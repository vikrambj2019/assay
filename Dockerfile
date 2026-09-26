# Official Playwright Python image: Chromium + system deps preinstalled,
# pinned to a Playwright version. No `playwright install` needed at build time.
# The image tag MUST match the pinned `playwright` version in requirements.txt,
# or the pip package and the preinstalled browser binaries drift apart.
FROM mcr.microsoft.com/playwright/python:v1.61.0-jammy

WORKDIR /app

# Node.js + Claude Code CLI — the Claude Agent SDK drives the CLI as a subprocess.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && npm install -g @anthropic-ai/claude-code \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# Install Python deps first for layer caching.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Install the package so the `agent` console script is on PATH.
COPY pyproject.toml ./
COPY core ./core
COPY harness ./harness
RUN pip install --no-cache-dir -e .

# Project files (examples, tests) are bind-mounted via docker-compose in dev,
# but copy them so the image is self-contained too.
COPY . .

ENTRYPOINT ["agent"]
CMD ["--help"]
