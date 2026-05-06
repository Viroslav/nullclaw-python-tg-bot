# nullclaw-python-tg-bot

Telegram bot for testing `nullwatch-py` hallucination detection with local models through Ollama.

The repository is intentionally lightweight: the main logic lives in `nullwatch-py`, while this repo is a runnable usage example with a Telegram UI built on `aiogram`.

## Layout

This repository is intended to live next to `nullwatch-py`:

```text
WB_HACKATON/
  nullwatch-py/
  nullclaw-python-tg-bot/
```

When started from this layout, the bot can import the sibling `nullwatch-py` checkout automatically.

## Setup

1. Create `.env` from `.env.example`
2. Create a local `.venv` in this repository
3. Install `aiogram` into that local environment
4. Ensure Ollama is running and the model is pulled
5. Optionally start `nullwatch`

```bash
cd /Users/nikolayivanov/Desktop/coding/WB/WB_HACKATON/nullclaw-python-tg-bot
python3.11 -m venv .venv
./.venv/bin/pip install aiogram
```

Example:

```bash
cd /Users/nikolayivanov/Desktop/coding/WB/WB_HACKATON/nullwatch
zig build run -- serve
```

```bash
cd /Users/nikolayivanov/Desktop/coding/WB/WB_HACKATON/nullclaw-python-tg-bot
./.venv/bin/python -u -m nullclaw_python_tg_bot
```

## Commands

- `/status`
- `/rag Who created Zig?`
- `/tool find documentation about Zig and Andrew Kelley`

Plain text is treated like `/rag`.
