# Use an official Python runtime as a parent image
FROM python:3.14-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

# Install system dependencies required for yt-dlp and ffmpeg
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    nodejs \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install uv
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:$PATH"

# Set the working directory in the container
WORKDIR /app

# Copy dependency files
COPY pyproject.toml .

# Install Python dependencies using uv
RUN uv pip install --system -r pyproject.toml

# Copy the rest of the application's code
COPY src/ .

# Command to run the application
CMD ["python", "main.py"]
