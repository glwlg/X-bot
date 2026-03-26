FROM node:22-alpine AS frontend-builder
WORKDIR /app
COPY src/platforms/web ./src/platforms/web
WORKDIR /app/src/platforms/web
RUN node -e "let pkg=require('./package.json'); delete pkg.overrides; require('fs').writeFileSync('package.json', JSON.stringify(pkg, null, 2))"
RUN npm install
RUN npm run build


FROM python:3.14-slim AS python-base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/root/.local/bin:$PATH" \
    UV_PYPI_MIRROR=https://pypi.tuna.tsinghua.edu.cn/simple

RUN sed -i 's/deb.debian.org/mirrors.tuna.tsinghua.edu.cn/g' /etc/apt/sources.list.d/debian.sources
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt/lists,sharing=locked \
    apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates
RUN curl -LsSf https://astral.sh/uv/install.sh | sh

WORKDIR /app
COPY pyproject.toml uv.lock ./

FROM python-base AS shared-runtime-python
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --system --group shared-runtime

FROM shared-runtime-python AS bot-runtime-python
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --system --group bot-runtime

FROM bot-runtime-python AS ikaros-python
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --system --group ikaros-runtime

FROM shared-runtime-python AS api-python
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --system --group api


FROM ikaros-python AS ikaros-runtime

RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt/lists,sharing=locked \
    apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    git \
    nodejs \
    npm \
    gnupg \
    procps \
    net-tools \
    iproute2 \
    && install -m 0755 -d /etc/apt/keyrings \
    && curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg -o /etc/apt/keyrings/githubcli-archive-keyring.gpg \
    && chmod a+r /etc/apt/keyrings/githubcli-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" > /etc/apt/sources.list.d/github-cli.list \
    && curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc \
    && chmod a+r /etc/apt/keyrings/docker.asc \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian bookworm stable" > /etc/apt/sources.list.d/docker.list \
    && apt-get update && apt-get install -y --no-install-recommends \
    gh \
    docker-ce-cli \
    docker-compose-plugin

RUN npm install -g \
    @openai/codex@latest \
    @google/gemini-cli@latest \
    && if ! command -v gemini-cli >/dev/null 2>&1; then \
    printf '#!/usr/bin/env sh\nexec gemini "$@"\n' > /usr/local/bin/gemini-cli; \
    chmod +x /usr/local/bin/gemini-cli; \
    fi

COPY src/ .

CMD ["python", "main.py"]


FROM ikaros-runtime AS ikaros-runtime-full
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --system --group optional-skill-runtime

FROM api-python AS api-runtime

COPY src/ .
COPY --from=frontend-builder /app/src/api/static/dist /app/api/static/dist

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8764"]
