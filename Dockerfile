# Use an official Python runtime as a parent image
FROM python:3.14-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/root/.local/bin:$PATH" \
    # UV Mirror for China
    UV_PYPI_MIRROR=https://pypi.tuna.tsinghua.edu.cn/simple \
    # Playwright/Patchright Browsers Path (for caching)
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    PLAYWRIGHT_CLI_BROWSER=chrome \
    PLAYWRIGHT_MCP_CONFIG=/app/playwright-cli.json

# Replace Debian sources with Tsinghua Mirror (for China speedup)
RUN sed -i 's/deb.debian.org/mirrors.tuna.tsinghua.edu.cn/g' /etc/apt/sources.list.d/debian.sources

# Install system dependencies
# ffmpeg: for audio/video processing
# nodejs, npm: for executing MCP Servers/Skills
# docker-ce-cli: for Docker-in-Docker operations
# Added --mount=type=cache to speed up apt installs
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt/lists,sharing=locked \
    apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    git \
    nodejs \
    npm \
    curl \
    ca-certificates \
    gnupg \
    procps \
    net-tools \
    iproute2 \
    # Install Docker CLI setup
    && install -m 0755 -d /etc/apt/keyrings \
    && curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc \
    && chmod a+r /etc/apt/keyrings/docker.asc \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian bookworm stable" > /etc/apt/sources.list.d/docker.list \
    && apt-get update && apt-get install -y --no-install-recommends \
    docker-ce-cli \
    docker-compose-plugin

# Install uv
RUN curl -LsSf https://astral.sh/uv/install.sh | sh

RUN npm install -g @playwright/cli@latest \
    && npx -y playwright install chrome

# Set the working directory in the container
WORKDIR /app

RUN printf '{\n  "browser": {\n    "launchOptions": {\n      "args": ["--no-sandbox", "--disable-setuid-sandbox"],\n      "channel": "chrome",\n      "chromiumSandbox": false\n    }\n  }\n}\n' > /app/playwright-cli.json

# Copy dependency files
COPY pyproject.toml .

# Install Python dependencies using uv with cache mount
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --system -r pyproject.toml

# Copy the rest of the application's code
COPY src/ .

# Command to run the application
CMD ["python", "main.py"]
