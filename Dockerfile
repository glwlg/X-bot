# Use an official Python runtime as a parent image
FROM python:3.14-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED# 安装系统依赖
# ffmpeg: 用于处理音视频
# nodejs, npm: 用于运行 MCP Servers (如 memory, playwright)
# docker-ce-cli: 用于 Docker-in-Docker (Playwright)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    git \
    nodejs \
    npm \
    curl \
    ca-certificates \
    gnupg \
    && rm -rf /var/lib/apt/lists/*

# Install Docker CLI only (for MCP)
RUN install -m 0755 -d /etc/apt/keyrings \
    && curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc \
    && chmod a+r /etc/apt/keyrings/docker.asc \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian bookworm stable" > /etc/apt/sources.list.d/docker.list \
    && apt-get update && apt-get install -y --no-install-recommends \
    docker-ce-cli \
    docker-compose-plugin \
    net-tools \
    iproute2 \
    && rm -rf /var/lib/apt/lists/*

# Install uv
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:$PATH"

# Set the working directory in the container
WORKDIR /app

# Copy dependency files
COPY pyproject.toml README.md .

# Install Python dependencies using uv
RUN uv pip install --system -r pyproject.toml

# Install Playwright/Patchright browsers and system dependencies
RUN apt-get update \
    && patchright install-deps chromium \
    && rm -rf /var/lib/apt/lists/* \
    && patchright install chromium

# Copy the rest of the application's code
COPY src/ .

# Command to run the application
CMD ["python", "main.py"]
