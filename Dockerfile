# syntax=docker/dockerfile:1
# Build context: WB_HACKATON/ (parent of nullclaw-python-tg-bot/)
# Telegram bot with nullwatch-py SDK installed from local source

FROM python:3.11-slim

WORKDIR /app
ENV PIP_DEFAULT_TIMEOUT=1000

# Preinstall CPU-only PyTorch to avoid downloading CUDA wheels on linux/arm64.
RUN pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu "torch>=2.0"

# Install nullwatch-py SDK from local source with RAG extras
COPY nullwatch-py/ /tmp/nullwatch-py/
RUN pip install --no-cache-dir "/tmp/nullwatch-py[rag]" && rm -rf /tmp/nullwatch-py/

# Install bot dependencies
COPY nullclaw-python-tg-bot/requirements.txt /tmp/bot-requirements.txt
RUN pip install --no-cache-dir -r /tmp/bot-requirements.txt && rm -f /tmp/bot-requirements.txt

# Copy bot source
COPY nullclaw-python-tg-bot/nullclaw_python_tg_bot/ ./nullclaw_python_tg_bot/

# Workspace for nullclaw agent memory (mounted as volume at runtime)
RUN mkdir -p /nullclaw-home/workspace
ENV NULLCLAW_HOME=/nullclaw-home
ENV NULLCLAW_WORKSPACE_DIR=/nullclaw-home/workspace

ENTRYPOINT ["python", "-u", "-m", "nullclaw_python_tg_bot"]
