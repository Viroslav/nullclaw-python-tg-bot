# nullclaw-python-tg-bot

Telegram bot that connects [nullclaw](https://github.com/nullclaw/nullclaw) to [nullwatch-py](https://github.com/nullclaw/nullwatch-python-sdk), demonstrating RAG hallucination detection and tool-call grounding scoring for AI agents.

`nullwatch-py` is an SDK, not a standalone daemon. In this stack it runs inside the bot container and sends spans/evals to the separate `nullwatch` backend service.

## Stack

| Service    | What it does                                              |
|------------|-----------------------------------------------------------|
| `nullwatch`| Observability backend — ingests spans and evals (port 7710) |
| `ollama`   | Local LLM runtime (port 11434)                           |
| `nullclaw` | AI agent gateway — memory, tools, A2A protocol (port 3000)|
| `bot`      | This Telegram bot, with [nullwatch-py](https://github.com/nullclaw/nullwatch-python-sdk) SDK |

## Quick start (Docker)

Requires:
- [Docker Desktop](https://docs.docker.com/get-docker/)
- a sibling checkout of `../nullwatch-py` so the bot image can install the local SDK source

Optional for local non-Docker development:
- `../nullclaw`
- `../nullwatch`

During `docker compose up --build` the images pull/build everything automatically:
- `nullwatch` — cloned from GitHub and compiled with Zig 0.16.0 inside Docker
- `nullclaw` — pulled as `ghcr.io/nullclaw/nullclaw:latest`
- `nullwatch-py` SDK — installed from the local sibling repo `../nullwatch-py`
- `ollama` + model — pulled and downloaded on first start

```bash
cd nullclaw-python-tg-bot

# 1. First-time setup (copies nullwatch-py, creates .env)
bash setup.sh

# 2. Fill in your Telegram bot token
echo 'BOT_TOKEN=your_token_here' >> .env

# 3. Start everything
docker compose up -d

# 4. Check status
docker compose ps
docker compose logs -f bot
```

First start downloads ~5 GB for `qwen3:8b`, so `docker compose up` may appear "stuck" for a long time while `ollama-pull` is still running. Check progress with `docker compose logs -f ollama-pull`.

`nullclaw` is considered healthy only after `GET /ready` returns `200 OK`. This avoids a common failure mode where `/health` is up but `/a2a` still answers `503 Service Unavailable`.

### Useful commands

```bash
docker compose logs -f          # all logs
docker compose logs -f bot      # bot only
docker compose ps               # health status
docker compose down             # stop (data preserved)
docker compose down -v          # full reset (deletes all data)
```

Or via `make`:

```bash
make setup    # first-time setup
make up       # start
make down     # stop
make logs     # follow logs
make logs-bot # bot logs only
make status   # health
make build    # rebuild after code changes
make reset-nullwatch   # wipe trace data only
make reset-nullclaw    # wipe agent memory only
make clean    # full reset
```

## Configuration

Copy `.env.example` → `.env` and set:

| Variable | Required | Default | Description |
|---|---|---|---|
| `BOT_TOKEN` | ✅ | — | From @BotFather |
| `OLLAMA_MODEL` | | `qwen3:8b` | Model to use and pull |
| `LLM_BACKEND` | | `nullclaw` | `nullclaw` or `ollama` |
| `TOOL_GROUNDING_BACKEND` | | `llm` | `llm` or `keyword` |
| `ENABLE_RAG_DETECTOR` | | `true` | Enables LettuceDetect-based `/rag` hallucination scoring |
| `HF_TOKEN` | | *(empty)* | Optional Hugging Face token for higher rate limits and faster model downloads |
| `NULLCLAW_PAIRING_CODE` | | *(empty)* | Leave empty — pairing disabled by default |

Service URLs (nullwatch, nullclaw, ollama) are automatically set to Docker service names inside the compose network. Only override them for local development without Docker.

The Docker stack creates the `nullclaw` home and workspace volumes automatically. You do not need to create a `nullclaw-test-home` directory by hand when using Compose.

## Bot commands

| Command | Description |
|---|---|
| `/rag <question>` | Answer from context + RAG hallucination check |
| `/tool <request>` | Tool call + schema and grounding validation |
| `/show_md <FILE.md>` | Show agent workspace markdown file |
| `/status` | Health check for all services |

In `nullclaw` mode, plain text messages go directly to the agent. If you ask it to remember something or update `IDENTITY.md` / other workspace markdown files, it should do that itself via memory/tools instead of separate admin commands.

`/rag` always produces an answer. Hallucination scoring is enabled by default and controlled by `ENABLE_RAG_DETECTOR`. If the detector is enabled but its heavy dependencies are not installed in the current runtime, the bot returns a clear `UNAVAILABLE` detector status instead of crashing.

If you see Hugging Face rate-limit warnings while the detector model is downloading, add `HF_TOKEN=...` to `.env`. The `bot` container already receives all values from `.env` via `env_file`, so no `docker-compose.yml` change is required.

## Local development (without Docker)

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
pip install -r ../nullwatch-py/requirements.txt

cp .env.example .env
# edit .env, then run services manually:

# Terminal 1 — nullwatch
cd ../nullwatch && zig build run -- serve

# Terminal 2 — ollama
ollama serve

# Terminal 3 — nullclaw
cd ../nullclaw
NULLCLAW_HOME=../nullclaw-python-tg-bot/docker/nullclaw-home zig build run -- gateway

# Terminal 4 — bot
cd ../nullclaw-python-tg-bot
.venv/bin/python -u -m nullclaw_python_tg_bot
```

## Project layout

```text
nullclaw-python-tg-bot/
  Dockerfile                      # bot image (includes nullwatch-py SDK)
  docker-compose.yml              # full stack orchestration
  Makefile                        # convenience targets
  setup.sh                        # first-time setup script
  .env.example                    # configuration template
  docker/
    Dockerfile.nullwatch           # builds nullwatch from source (Zig 0.16.0)
    nullclaw-home/
      config.json                  # nullclaw config for Docker (uses service names)
      workspace/                   # agent memory files (persisted as Docker volume)
  nullclaw_python_tg_bot/
    bot.py                         # main bot logic
    nullclaw_gateway.py            # nullclaw A2A client
  tests/
```
