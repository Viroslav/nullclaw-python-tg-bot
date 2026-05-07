# syntax=docker/dockerfile:1
# Build context: WB_HACKATON/ (parent of nullclaw-python-tg-bot/)
# Telegram bot with nullwatch-py SDK installed from local source

FROM python:3.11-slim

WORKDIR /app

# Install nullwatch-py SDK from local source (no PyPI release yet)
COPY nullwatch-py/ /tmp/nullwatch-py/
RUN pip install --no-cache-dir /tmp/nullwatch-py/ && rm -rf /tmp/nullwatch-py/

# Install bot dependencies
RUN pip install --no-cache-dir aiogram python-dotenv

# Copy bot source
COPY nullclaw-python-tg-bot/nullclaw_python_tg_bot/ ./nullclaw_python_tg_bot/

# Workspace for nullclaw agent memory (mounted as volume at runtime)
RUN mkdir -p /nullclaw-home/workspace
ENV NULLCLAW_HOME=/nullclaw-home
ENV NULLCLAW_WORKSPACE_DIR=/nullclaw-home/workspace

ENTRYPOINT ["python", "-u", "-m", "nullclaw_python_tg_bot"]
