# nullclaw-python-tg-bot

## Requirements

- Python 3.11+
- Zig
- Ollama
- Pulled model, for example `qwen3:8b`
- Running checkouts next to each other:

```text
local_folder/
  nullwatch/
  nullwatch-py/
  nullclaw/
  nullclaw-test-home/
  nullclaw-python-tg-bot/
```

## Setup

```bash
cd local_folder/nullclaw-python-tg-bot
python3 -m venv .venv
./.venv/bin/pip install aiogram python-dotenv
cp .env.example .env
```

Recommended `.env` values:

```bash
LLM_BACKEND=nullclaw
OLLAMA_URL=http://127.0.0.1:11434
OLLAMA_MODEL=qwen3:8b
NULLWATCH_URL=http://127.0.0.1:7710
NULLCLAW_URL=http://127.0.0.1:3000
TOOL_GROUNDING_BACKEND=llm
TOOL_GROUNDING_LLM_URL=http://127.0.0.1:11434/v1
TOOL_GROUNDING_MODEL=qwen3:8b
```

Also set:

- `BOT_TOKEN`
- `NULLCLAW_PAIRING_CODE` or `NULLCLAW_BEARER_TOKEN`

## Start Processes

1. Start `nullwatch` (In first terminal)

```bash
cd local_folder/nullwatch
zig build run -- serve
```

2. Start Ollama (In second terminal)

```bash
ollama serve
```

3. Start `nullclaw gateway` (In third terminal)

```bash
cd local_folder/nullclaw
NULLCLAW_HOME=local_folder/nullclaw-test-home zig build run -- gateway
```

4. Start Telegram bot (In fourth terminal)

```bash
cd local_folder/nullclaw-python-tg-bot
./.venv/bin/python -u -m nullclaw_python_tg_bot
```

## Stop Processes

Stop running processes:

```bash
pkill -f nullclaw_python_tg_bot || true
lsof -ti tcp:3000 | xargs kill -9 2>/dev/null || true
lsof -ti tcp:7710 | xargs kill -9 2>/dev/null || true
lsof -ti tcp:11434 | xargs kill -9 2>/dev/null || true
```

Clean `nullclaw` runtime state:

```bash
rm -f local_folder/nullclaw-test-home/workspace/memory/*.md
rm -f local_folder/nullclaw-test-home/workspace/.nullclaw/workspace-state.json
rm -f local_folder/nullclaw-test-home/daemon_state.json
rm -f local_folder/nullclaw-test-home/llm_token_usage.jsonl
```

Clean `nullwatch` traces and evals:

```bash
rm -rf /Users/nikolayivanov/.nullwatch/data/*
```
