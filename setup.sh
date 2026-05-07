#!/usr/bin/env bash
# setup.sh — one-time setup before 'docker compose up'
# Run from the nullclaw-python-tg-bot directory.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

echo "🔧 nullwatch-py hackathon stack — setup"
echo ""

# ── 1. Check Docker ───────────────────────────────────────────────────────────
if ! command -v docker &>/dev/null; then
  echo "❌ Docker not found. Install from https://docs.docker.com/get-docker/"
  exit 1
fi
if ! docker compose version &>/dev/null; then
  echo "❌ Docker Compose v2 required ('docker compose', not 'docker-compose')."
  exit 1
fi
echo "✅ Docker $(docker --version | cut -d' ' -f3 | tr -d ',')"

# ── 2. Check sibling repos ────────────────────────────────────────────────────
missing=0

if [ ! -d "$ROOT/../nullwatch-py" ]; then
  echo "❌ ../nullwatch-py not found. Clone it:"
  echo "   git clone https://github.com/Viroslav/nullwatch-py ../nullwatch-py"
  missing=1
else
  echo "✅ ../nullwatch-py found"
fi

if [ ! -d "$ROOT/../nullclaw" ]; then
  echo "ℹ️  ../nullclaw not found."
  echo "   This is fine for Docker Compose."
  echo "   Clone it only if you also want local development without Docker:"
  echo "   git clone https://github.com/nullclaw/nullclaw ../nullclaw"
else
  echo "✅ ../nullclaw found"
fi

[ "$missing" -eq 0 ] || exit 1

# ── 3. Create .env if missing ─────────────────────────────────────────────────
if [ ! -f "$ROOT/.env" ]; then
  cp "$ROOT/.env.example" "$ROOT/.env"
  echo "⚠️  Created .env from .env.example — fill in BOT_TOKEN before 'docker compose up'"
else
  echo "✅ .env exists"
fi

BOT_TOKEN_VAL="$(grep -E '^BOT_TOKEN=' "$ROOT/.env" | cut -d= -f2- | tr -d '"' | tr -d "'" | xargs 2>/dev/null || true)"
if [ -z "$BOT_TOKEN_VAL" ]; then
  echo ""
  echo "⚠️  BOT_TOKEN is empty — the bot won't start without it."
  echo "   Get a token from @BotFather and set it in .env"
fi

# ── 4. Workspace dir ──────────────────────────────────────────────────────────
mkdir -p "$ROOT/docker/nullclaw-home/workspace"
echo "✅ docker/nullclaw-home/workspace ready"

# ── 5. Done ───────────────────────────────────────────────────────────────────
echo ""
echo "All set. Run:"
echo ""
echo "  docker compose up -d --build   # first time"
echo "  docker compose up -d           # subsequent starts (uses cached images)"
echo "  docker compose logs -f         # follow logs"
echo "  docker compose ps              # check health"
echo ""
echo "First build: compiles nullwatch with Zig (~3 min), downloads qwen3:8b (~5 GB)."
echo "Subsequent starts are instant — everything is cached."
