import asyncio
import html
import os
import sys
import time
import urllib.request
from pathlib import Path
from typing import Iterable

from dotenv import load_dotenv


def _bootstrap_sibling_nullwatch_py() -> None:
    try:
        import nullwatch  # noqa: F401
        return
    except ImportError:
        pass

    current = Path(__file__).resolve()
    sibling = current.parents[2] / "nullwatch-py"
    if sibling.exists():
        sys.path.insert(0, str(sibling))


_bootstrap_sibling_nullwatch_py()

from aiogram import Bot, Dispatcher, Router  # noqa: E402
from aiogram.enums import ChatAction, ParseMode  # noqa: E402
from aiogram.filters import Command  # noqa: E402
from aiogram.types import Message  # noqa: E402
from nullwatch import NullwatchClient  # noqa: E402
from nullwatch.scorers import (  # noqa: E402
    RAGHallucinationScorer,
    ToolCallGroundingScorer,
    ToolCallScorer,
)

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
BOT_NAME = os.getenv("BOT_NAME", "nullwatch-bot").strip()
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen3:0.6b").strip()
NULLWATCH_URL = os.getenv("NULLWATCH_URL", "http://127.0.0.1:7710").rstrip("/")

if not BOT_TOKEN:
    raise SystemExit("BOT_TOKEN is required in .env")

RUN_PREFIX = "telegram-demo"

CORPUS = [
    {
        "id": "zig",
        "text": (
            "The Zig programming language was created by Andrew Kelley. "
            "Zig 0.14.0 was released in March 2025. "
            "Zig emphasizes simplicity, performance, and explicit memory management."
        ),
    },
    {
        "id": "python",
        "text": (
            "Python was created by Guido van Rossum and first released in 1991. "
            "Python is known for readability and a large ecosystem."
        ),
    },
    {
        "id": "nullwatch",
        "text": (
            "nullwatch is the execution-intelligence layer in the null stack. "
            "It ingests traces and eval results and exposes them through a JSON HTTP API."
        ),
    },
]

TOOLS_SCHEMA = [
    {
        "type": "function",
        "function": {
            "name": "search_docs",
            "description": "Search the local documentation snippets for a topic",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "max_results": {"type": "integer", "minimum": 1, "maximum": 5},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
    }
]

router = Router()
client = NullwatchClient(base_url=NULLWATCH_URL, raise_on_error=False, default_source="telegram-bot")


def post_ollama(messages: list[dict], tools: list[dict] | None = None) -> dict:
    import json

    payload: dict = {
        "model": OLLAMA_MODEL,
        "messages": messages,
        "stream": False,
        "options": {"think": False},
    }
    if tools:
        payload["tools"] = tools

    req = urllib.request.Request(
        f"{OLLAMA_URL}/api/chat",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        raw = resp.read().decode()
        return json.loads(raw) if raw else {}


def keyword_score(question: str, doc_text: str) -> int:
    words = {w.lower() for w in question.replace("/", " ").split() if len(w) >= 3}
    hay = doc_text.lower()
    return sum(1 for w in words if w in hay)


def retrieve_context(question: str, top_k: int = 2) -> list[str]:
    ranked = sorted(CORPUS, key=lambda doc: keyword_score(question, doc["text"]), reverse=True)
    picked = [doc["text"] for doc in ranked[:top_k] if keyword_score(question, doc["text"]) > 0]
    return picked or [CORPUS[0]["text"]]


def format_context(contexts: Iterable[str]) -> str:
    return "\n".join(f"• {item}" for item in contexts)


def escape(text: str) -> str:
    return html.escape(text, quote=False)


def verdict_badge(verdict: str) -> str:
    return "🟢 PASS" if verdict == "pass" else "🔴 FAIL"


def status_badge(ok: bool) -> str:
    return "🟢 OK" if ok else "🔴 DOWN"


def parse_command(text: str) -> tuple[str, str]:
    stripped = text.strip()
    if not stripped:
        return "help", ""
    if stripped.startswith("/start"):
        return "start", ""
    if stripped.startswith("/help"):
        return "help", ""
    if stripped.startswith("/status"):
        return "status", ""
    if stripped.startswith("/rag "):
        return "rag", stripped[5:].strip()
    if stripped == "/rag":
        return "rag", ""
    if stripped.startswith("/tool "):
        return "tool", stripped[6:].strip()
    if stripped == "/tool":
        return "tool", ""
    return "rag", stripped


def build_help() -> str:
    return (
        f"🤖 <b>{escape(BOT_NAME)}</b>\n"
        f"Model: <code>{escape(OLLAMA_MODEL)}</code>\n\n"
        "Доступные команды:\n"
        "• <code>/rag &lt;question&gt;</code> — ответ + проверка RAG hallucination\n"
        "• <code>/tool &lt;request&gt;</code> — tool call + schema/grounding checks\n"
        "• <code>/status</code> — состояние сервисов\n"
        "• <code>/help</code> — подсказка\n\n"
        "💡 Обычный текст тоже считается как <code>/rag</code>."
    )


def process_rag(chat_id: int, question: str) -> str:
    question = question.strip()
    if not question:
        return "⚠️ Отправь <code>/rag твой вопрос</code>."

    contexts = retrieve_context(question)
    run_id = f"{RUN_PREFIX}-rag-{chat_id}-{int(time.time())}"
    prompt = (
        "Answer the following question based ONLY on the provided context.\n\n"
        f"Context:\n{format_context(contexts)}\n\n"
        f"Question: {question}\n\nAnswer:"
    )

    with client.span(run_id, "llm.call", source="telegram-bot", model=OLLAMA_MODEL) as span:
        response = post_ollama([{"role": "user", "content": prompt}])
        answer = response["message"]["content"].strip()
        if "<think>" in answer:
            answer = answer.split("</think>")[-1].strip()
        span.input_tokens = response.get("prompt_eval_count")
        span.output_tokens = response.get("eval_count")

    eval_result = RAGHallucinationScorer().score(
        run_id=run_id,
        contexts=contexts,
        question=question,
        answer=answer,
    )
    client.ingest_eval(eval_result)

    return (
        f"🧠 <b>RAG Check</b>\n"
        f"Status: <b>{verdict_badge(eval_result.verdict)}</b>\n"
        f"Score: <code>{eval_result.score:.3f}</code>\n\n"
        f"❓ <b>Question</b>\n<blockquote>{escape(question)}</blockquote>\n\n"
        f"💬 <b>Answer</b>\n<blockquote>{escape(answer)}</blockquote>\n\n"
        f"🔎 <b>Detector</b>\n<blockquote>{escape(eval_result.notes or '')}</blockquote>\n\n"
        f"📚 <b>Context</b>\n<blockquote>{escape(format_context(contexts))}</blockquote>"
    )


def process_tool(chat_id: int, request_text: str) -> str:
    request_text = request_text.strip()
    if not request_text:
        return "⚠️ Отправь <code>/tool твой запрос</code>."

    contexts = retrieve_context(request_text)
    run_id = f"{RUN_PREFIX}-tool-{chat_id}-{int(time.time())}"
    tool_prompt = (
        "You are a helpful assistant. Use the search_docs tool when helpful. "
        f"User request: {request_text}"
    )

    with client.span(run_id, "llm.call", source="telegram-bot", model=OLLAMA_MODEL) as span:
        response = post_ollama([{"role": "user", "content": tool_prompt}], tools=TOOLS_SCHEMA)
        span.input_tokens = response.get("prompt_eval_count")
        span.output_tokens = response.get("eval_count")

    message = response.get("message", {})
    tool_calls = message.get("tool_calls", [])
    if not tool_calls:
        notes = message.get("content", "").strip() or "Model returned no tool call."
        empty_eval = ToolCallScorer(tools=TOOLS_SCHEMA).score(run_id=run_id)
        client.ingest_eval(empty_eval)
        return (
            "🛠️ <b>Tool Check</b>\n"
            f"Status: <b>{verdict_badge('fail')}</b>\n\n"
            "Модель не вернула tool call.\n\n"
            f"📄 <b>Raw response</b>\n<blockquote>{escape(notes)}</blockquote>"
        )

    schema_eval = ToolCallScorer(tools=TOOLS_SCHEMA).score(run_id=run_id, tool_calls=tool_calls)
    grounding_eval = ToolCallGroundingScorer(context=contexts, backend="keyword").score(
        run_id=run_id,
        tool_calls=tool_calls,
    )
    client.ingest_eval(schema_eval)
    client.ingest_eval(grounding_eval)

    rendered_calls = []
    for call in tool_calls:
        fn = call.get("function", call)
        rendered_calls.append(f"• {fn.get('name')} {fn.get('arguments')}")

    return (
        "🛠️ <b>Tool Check</b>\n"
        f"Schema: <b>{verdict_badge(schema_eval.verdict)}</b> <code>{schema_eval.score:.3f}</code>\n"
        f"Grounding: <b>{verdict_badge(grounding_eval.verdict)}</b> <code>{grounding_eval.score:.3f}</code>\n\n"
        f"📝 <b>Request</b>\n<blockquote>{escape(request_text)}</blockquote>\n\n"
        f"🔧 <b>Tool Calls</b>\n<blockquote>{escape(chr(10).join(rendered_calls))}</blockquote>\n\n"
        f"📐 <b>Schema notes</b>\n<blockquote>{escape(schema_eval.notes or '')}</blockquote>\n\n"
        f"🧭 <b>Grounding notes</b>\n<blockquote>{escape(grounding_eval.notes or '')}</blockquote>\n\n"
        f"📚 <b>Context</b>\n<blockquote>{escape(format_context(contexts))}</blockquote>"
    )


def process_status() -> str:
    ollama_ok = True
    try:
        with urllib.request.urlopen(f"{OLLAMA_URL}/api/tags", timeout=5) as resp:
            ollama_ok = resp.status == 200
    except Exception:
        ollama_ok = False

    nullwatch_ok = client.is_alive()
    return (
        "📊 <b>Status</b>\n"
        f"Ollama: <b>{status_badge(ollama_ok)}</b>\n"
        f"Model: <code>{escape(OLLAMA_MODEL)}</code>\n"
        f"nullwatch: <b>{status_badge(nullwatch_ok)}</b>\n"
        f"Bot: <code>{escape(BOT_NAME)}</code>"
    )


async def run_blocking(message: Message, fn, *args) -> str:
    await message.bot.send_chat_action(message.chat.id, ChatAction.TYPING, message_thread_id=message.message_thread_id)
    return await asyncio.to_thread(fn, *args)


@router.message(Command("start", "help"))
async def help_handler(message: Message) -> None:
    await message.answer(build_help(), parse_mode=ParseMode.HTML, disable_web_page_preview=True)


@router.message(Command("status"))
async def status_handler(message: Message) -> None:
    reply = await run_blocking(message, process_status)
    await message.answer(reply, parse_mode=ParseMode.HTML, disable_web_page_preview=True)


@router.message(Command("rag"))
async def rag_handler(message: Message) -> None:
    command, payload = parse_command(message.text or "")
    del command
    reply = await run_blocking(message, process_rag, message.chat.id, payload)
    await message.answer(reply, parse_mode=ParseMode.HTML, disable_web_page_preview=True)


@router.message(Command("tool"))
async def tool_handler(message: Message) -> None:
    command, payload = parse_command(message.text or "")
    del command
    reply = await run_blocking(message, process_tool, message.chat.id, payload)
    await message.answer(reply, parse_mode=ParseMode.HTML, disable_web_page_preview=True)


@router.message()
async def fallback_handler(message: Message) -> None:
    if not message.text:
        return
    reply = await run_blocking(message, process_rag, message.chat.id, message.text)
    await message.answer(reply, parse_mode=ParseMode.HTML, disable_web_page_preview=True)


async def amain() -> int:
    bot = Bot(BOT_TOKEN)
    dp = Dispatcher()
    dp.include_router(router)

    print(f"🤖 Starting {BOT_NAME} with model {OLLAMA_MODEL}")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)
    return 0


def main() -> int:
    try:
        return asyncio.run(amain())
    except KeyboardInterrupt:
        print("Bot stopped")
        return 0
