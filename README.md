# nullclaw-python-tg-bot

Telegram bot for testing `nullwatch-py` hallucination detection with local models through Ollama or through `nullclaw` gateway.

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
5. Start `nullwatch`
6. Optionally start `nullclaw gateway` and switch `LLM_BACKEND=nullclaw`

```bash
cd /nullclaw-python-tg-bot
```

Example:

```bash
cd /nullwatch
zig build run -- serve
```

Optional `nullclaw` path:

```bash
cd /nullclaw
CODEX_HOME=/nullclaw-test-home zig build run -- gateway
```

Then set these in `.env`:

```bash
LLM_BACKEND=nullclaw
NULLCLAW_URL=http://127.0.0.1:3000
NULLCLAW_PAIRING_CODE=... # one-time code printed by gateway startup
TOOL_GROUNDING_BACKEND=llm
TOOL_GROUNDING_LLM_URL=http://127.0.0.1:11434/v1
TOOL_GROUNDING_MODEL=qwen3:0.6b
```

```bash
cd /nullclaw-python-tg-bot
./.venv/bin/python -u -m nullclaw_python_tg_bot
```

## Commands

- `/agent remember that my name is Nikolay`
- `/status`
- `/rag Who created Zig?`
- `/tool find documentation about Zig and Andrew Kelley`

Plain text is treated like `/agent` in `LLM_BACKEND=nullclaw` mode and like `/rag` in `LLM_BACKEND=ollama` mode.

## Notes

- In `LLM_BACKEND=ollama` mode, `/tool` validates raw OpenAI-style tool calls returned by the model.
- In `LLM_BACKEND=nullclaw` mode, `/agent` is the main path for testing memory, skills, and general agent behavior.
- In `LLM_BACKEND=nullclaw` mode, `/tool` evaluates observed tool execution events coming from `nullclaw` + `nullwatch`. This is closer to real agent behavior, but A2A responses do not expose the original OpenAI-style schema payload verbatim.
- The bot correlates `nullclaw` gateway responses with recent OTEL runs in `nullwatch`, because current A2A spans do not carry a stable `session_id`.
- For `nullclaw` mode, `TOOL_GROUNDING_BACKEND=llm` is recommended. It is slower than the keyword heuristic, but handles operational tool requests much better.
- Tool execution still depends on the workspace configured for the running `nullclaw gateway`. If the gateway workspace is not your project root, shell/file tools may fail on `cwd` or allowed-path checks even though the SDK wiring itself is correct.
