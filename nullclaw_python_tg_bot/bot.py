import asyncio
import html
import json
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
from nullwatch import Eval, NullwatchClient  # noqa: E402
from nullwatch.scorers import (  # noqa: E402
    RAGHallucinationScorer,
    ToolCallGroundingScorer,
    ToolCallScorer,
)

from .nullclaw_gateway import NullclawGatewayClient, NullclawGatewayError  # noqa: E402

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
BOT_NAME = os.getenv("BOT_NAME", "nullwatch-bot").strip()
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen3:0.6b").strip()
NULLWATCH_URL = os.getenv("NULLWATCH_URL", "http://127.0.0.1:7710").rstrip("/")
LLM_BACKEND = os.getenv("LLM_BACKEND", "ollama").strip().lower() or "ollama"
NULLCLAW_URL = os.getenv("NULLCLAW_URL", "http://127.0.0.1:3000").rstrip("/")
NULLCLAW_PAIRING_CODE = os.getenv("NULLCLAW_PAIRING_CODE", "").strip()
NULLCLAW_BEARER_TOKEN = os.getenv("NULLCLAW_BEARER_TOKEN", "").strip()
NULLCLAW_CHANNEL = os.getenv("NULLCLAW_CHANNEL", "nullwatch-bot").strip() or "nullwatch-bot"
TOOL_GROUNDING_BACKEND = (
    os.getenv("TOOL_GROUNDING_BACKEND", "llm" if LLM_BACKEND == "nullclaw" else "keyword").strip().lower()
    or ("llm" if LLM_BACKEND == "nullclaw" else "keyword")
)
TOOL_GROUNDING_LLM_URL = os.getenv("TOOL_GROUNDING_LLM_URL", f"{OLLAMA_URL}/v1").rstrip("/")
TOOL_GROUNDING_MODEL = os.getenv("TOOL_GROUNDING_MODEL", OLLAMA_MODEL).strip() or OLLAMA_MODEL

if LLM_BACKEND not in {"ollama", "nullclaw"}:
    raise SystemExit("LLM_BACKEND must be 'ollama' or 'nullclaw'")
if TOOL_GROUNDING_BACKEND not in {"keyword", "llm"}:
    raise SystemExit("TOOL_GROUNDING_BACKEND must be 'keyword' or 'llm'")

if not BOT_TOKEN:
    raise SystemExit("BOT_TOKEN is required in .env")

RUN_PREFIX = "telegram-demo"
NULLCLAW_SPAN_LIMIT = 200
NULLCLAW_SPAN_POLL_ATTEMPTS = 8
NULLCLAW_SPAN_POLL_DELAY_SECS = 0.15
NULLCLAW_RUNTIME_OPERATIONS = {
    "agent.start",
    "llm.request",
    "llm.response",
    "metric.tokens_used",
    "tool.start",
    "tool.call",
    "turn.complete",
}

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
nullclaw = NullclawGatewayClient(
    base_url=NULLCLAW_URL,
    pairing_code=NULLCLAW_PAIRING_CODE,
    bearer_token=NULLCLAW_BEARER_TOKEN,
    default_channel=NULLCLAW_CHANNEL,
)


def post_ollama(messages: list[dict], tools: list[dict] | None = None) -> dict:
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


def strip_thinking(text: str) -> str:
    if "<think>" in text:
        return text.split("</think>")[-1].strip()
    return text.strip()


def model_label() -> str:
    return f"nullclaw/{OLLAMA_MODEL}" if LLM_BACKEND == "nullclaw" else OLLAMA_MODEL


def build_session_key(run_id: str) -> str:
    return nullclaw.build_session_key(run_id)


def as_int(value: object, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def unwrap_otlp_value(value: object) -> object:
    if not isinstance(value, dict):
        return value

    for key in ("stringValue", "boolValue", "intValue", "doubleValue"):
        if key in value:
            return value[key]

    if "arrayValue" in value and isinstance(value["arrayValue"], dict):
        values = value["arrayValue"].get("values", [])
        if isinstance(values, list):
            return [unwrap_otlp_value(item) for item in values]

    if "kvlistValue" in value and isinstance(value["kvlistValue"], dict):
        entries = value["kvlistValue"].get("values", [])
        if isinstance(entries, list):
            flattened: dict[str, object] = {}
            for item in entries:
                if not isinstance(item, dict):
                    continue
                key = item.get("key")
                if isinstance(key, str):
                    flattened[key] = unwrap_otlp_value(item.get("value"))
            return flattened

    return value


def parse_attributes_json(raw: object) -> dict:
    if not raw:
        return {}
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str):
        return {}

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}

    if isinstance(parsed, dict):
        return parsed
    if isinstance(parsed, list):
        flattened = {}
        for item in parsed:
            if not isinstance(item, dict):
                continue
            key = item.get("key")
            value = item.get("value")
            if isinstance(key, str):
                flattened[key] = unwrap_otlp_value(value)
        return flattened
    return {}


def decode_jsonish(value: object, depth: int = 3) -> object:
    current = value
    for _ in range(depth):
        if not isinstance(current, str):
            break
        try:
            current = json.loads(current)
        except json.JSONDecodeError:
            break
    return current


def parse_tool_arguments(attributes: dict) -> dict:
    raw_args = decode_jsonish(attributes.get("args"))
    if raw_args is None:
        return {}
    if isinstance(raw_args, dict):
        return raw_args
    if isinstance(raw_args, str):
        parsed = decode_jsonish(raw_args)
        return parsed if isinstance(parsed, dict) else {"_raw": raw_args}
    return {"_raw": raw_args}


def parse_tool_detail(attributes: dict) -> str | None:
    detail = decode_jsonish(attributes.get("detail"))
    if detail is None:
        return None
    if isinstance(detail, str):
        return detail
    return json.dumps(detail, ensure_ascii=False)


def find_recent_nullclaw_run(started_at_ms: int) -> tuple[str | None, str | None, list[dict]]:
    for _ in range(NULLCLAW_SPAN_POLL_ATTEMPTS):
        spans = client.list_spans(limit=NULLCLAW_SPAN_LIMIT)
        candidates = []
        for span in spans:
            operation = span.get("operation")
            source = str(span.get("source") or "")
            if operation not in NULLCLAW_RUNTIME_OPERATIONS:
                continue
            if not source or source == client.default_source:
                continue
            if as_int(span.get("stored_at_ms")) < started_at_ms:
                continue
            if not span.get("run_id"):
                continue
            candidates.append(span)

        if candidates:
            grouped: dict[tuple[str, str], list[dict]] = {}
            for span in candidates:
                key = (str(span.get("source")), str(span.get("run_id")))
                grouped.setdefault(key, []).append(span)

            best_key = max(
                grouped,
                key=lambda key: max(as_int(span.get("stored_at_ms")) for span in grouped[key]),
            )
            best_spans = sorted(grouped[best_key], key=lambda span: as_int(span.get("stored_at_ms")))
            return best_key[1], best_key[0], best_spans
        time.sleep(NULLCLAW_SPAN_POLL_DELAY_SECS)
    return None, None, []


def extract_nullclaw_tool_calls(started_at_ms: int) -> tuple[list[dict], dict]:
    observed_run_id, observed_source, spans = find_recent_nullclaw_run(started_at_ms)
    tool_calls: list[dict] = []

    for span in spans:
        if span.get("operation") != "tool.call":
            continue
        name = span.get("tool_name")
        if not name:
            continue
        attributes = parse_attributes_json(span.get("attributes_json"))
        status = str(span.get("status") or "")
        detail = parse_tool_detail(attributes)
        tool_calls.append(
            {
                "name": name,
                "arguments": parse_tool_arguments(attributes),
                "function": {
                    "name": name,
                    "arguments": parse_tool_arguments(attributes),
                },
                "meta": {
                    "source": "nullclaw-span",
                    "run_id": observed_run_id,
                    "observed_source": observed_source,
                    "status": status,
                    "detail": detail,
                },
            }
        )

    return tool_calls, {
        "observed_run_id": observed_run_id,
        "observed_source": observed_source,
        "observed_span_count": len(spans),
    }


def invoke_model(run_id: str, messages: list[dict], tools: list[dict] | None = None) -> dict:
    if LLM_BACKEND == "ollama":
        return post_ollama(messages, tools=tools)

    prompt_parts = [msg.get("content", "").strip() for msg in messages if msg.get("content")]
    prompt = "\n\n".join(part for part in prompt_parts if part)
    if tools:
        prompt += (
            "\n\nTooling note:\n"
            "If a tool is helpful, use it instead of guessing. "
            "Relevant function schema examples:\n"
            f"{json.dumps(tools, ensure_ascii=False)}"
        )

    started_at_ms = int(time.time() * 1000)
    response = nullclaw.send_message(
        prompt,
        context_id=run_id,
    )
    tool_calls, observed = extract_nullclaw_tool_calls(started_at_ms)
    return {
        "message": {
            "content": strip_thinking(str(response.get("response") or "")),
            "tool_calls": tool_calls,
        },
        "session_key": response.get("session_key", build_session_key(run_id)),
        "raw_response": response,
        **observed,
    }


def build_nullclaw_tool_eval(
    run_id: str,
    tool_calls: list[dict],
    *,
    observed_run_id: str | None = None,
    observed_source: str | None = None,
    observed_span_count: int = 0,
) -> Eval:
    base_meta = {
        "backend": "nullclaw",
        "observed_run_id": observed_run_id,
        "observed_source": observed_source,
        "observed_span_count": observed_span_count,
    }
    if not observed_run_id:
        return Eval(
            run_id=run_id,
            eval_key="tool_call_validity",
            scorer="nullclaw-observed-tools",
            score=0.0,
            verdict="fail",
            notes=(
                "Could not correlate this nullclaw gateway response with a recent OTEL run in nullwatch. "
                "Tool-call evaluation is therefore inconclusive for this request."
            ),
            meta=base_meta,
        )

    if not tool_calls:
        return Eval(
            run_id=run_id,
            eval_key="tool_call_validity",
            scorer="nullclaw-observed-tools",
            score=0.0,
            verdict="fail",
            notes=(
                "Observed the nullclaw run in nullwatch, but there were no tool.call spans for this request. "
                "The model likely answered directly without executing tools."
            ),
            meta={**base_meta, "tool_count": 0},
        )

    names = [call.get("name", "<unknown>") for call in tool_calls]
    failures = []
    for call in tool_calls:
        meta = call.get("meta", {})
        if meta.get("status") == "error":
            detail = meta.get("detail") or "tool execution failed"
            failures.append(f"{call.get('name', '<unknown>')}: {detail}")

    if failures:
        success_count = len(tool_calls) - len(failures)
        return Eval(
            run_id=run_id,
            eval_key="tool_call_validity",
            scorer="nullclaw-observed-tools",
            score=success_count / len(tool_calls),
            verdict="fail",
            notes="Observed tool execution failures: " + "; ".join(failures),
            meta={
                **base_meta,
                "tool_count": len(tool_calls),
                "tool_names": names,
                "failed_tools": failures,
            },
        )

    return Eval(
        run_id=run_id,
        eval_key="tool_call_validity",
        scorer="nullclaw-observed-tools",
        score=1.0,
        verdict="pass",
        notes=(
            "Observed real nullclaw tool execution events: "
            + ", ".join(names)
            + ". Raw OpenAI-style schema validation is not available over A2A, "
            "so this check is based on observed execution spans."
        ),
        meta={**base_meta, "tool_count": len(tool_calls), "tool_names": names},
    )


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
    if stripped.startswith("/agent "):
        return "agent", stripped[7:].strip()
    if stripped == "/agent":
        return "agent", ""
    if stripped.startswith("/rag "):
        return "rag", stripped[5:].strip()
    if stripped == "/rag":
        return "rag", ""
    if stripped.startswith("/tool "):
        return "tool", stripped[6:].strip()
    if stripped == "/tool":
        return "tool", ""
    return ("agent" if LLM_BACKEND == "nullclaw" else "rag"), stripped


def build_help() -> str:
    return (
        f"🤖 <b>{escape(BOT_NAME)}</b>\n"
        f"Backend: <code>{escape(LLM_BACKEND)}</code>\n"
        f"Model: <code>{escape(model_label())}</code>\n\n"
        "Доступные команды:\n"
        "• <code>/agent &lt;message&gt;</code> — прямой запрос в nullclaw для memory/skills/tools\n"
        "• <code>/rag &lt;question&gt;</code> — ответ + проверка RAG hallucination\n"
        "• <code>/tool &lt;request&gt;</code> — tool call + schema/grounding checks\n"
        "• <code>/status</code> — состояние сервисов\n"
        "• <code>/help</code> — подсказка\n\n"
        "💡 В режиме <code>nullclaw</code> обычный текст идет как <code>/agent</code>. "
        "В режиме <code>ollama</code> обычный текст считается как <code>/rag</code>."
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

    with client.span(run_id, "llm.call", source="telegram-bot", model=model_label()) as span:
        response = invoke_model(run_id, [{"role": "user", "content": prompt}])
        answer = strip_thinking(response["message"]["content"])
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


def render_observed_tool_calls(tool_calls: list[dict]) -> str:
    rendered_calls = []
    for call in tool_calls:
        fn = call.get("function", call)
        meta = call.get("meta", {})
        line = f"• {fn.get('name')} {fn.get('arguments')}"
        if meta.get("status"):
            line += f" [{meta.get('status')}]"
        if meta.get("detail"):
            line += f" -> {meta.get('detail')}"
        rendered_calls.append(line)
    return chr(10).join(rendered_calls)


def process_agent(chat_id: int, request_text: str) -> str:
    request_text = request_text.strip()
    if not request_text:
        return "⚠️ Отправь <code>/agent твой запрос</code>."

    run_id = f"{RUN_PREFIX}-agent-{chat_id}-{int(time.time())}"
    with client.span(run_id, "agent.chat", source="telegram-bot", model=model_label()) as span:
        response = invoke_model(run_id, [{"role": "user", "content": request_text}])
        span.input_tokens = response.get("prompt_eval_count")
        span.output_tokens = response.get("eval_count")

    message = response.get("message", {})
    answer = strip_thinking(str(message.get("content") or "")).strip() or "Пустой ответ."
    tool_calls = message.get("tool_calls", [])
    observed_run_id = response.get("observed_run_id")
    observed_source = response.get("observed_source")

    parts = [
        "💬 <b>Agent Reply</b>",
        f"Backend: <code>{escape(LLM_BACKEND)}</code>",
        f"Run: <code>{escape(str(observed_run_id or run_id))}</code>",
        f"Source: <code>{escape(str(observed_source or 'telegram-bot'))}</code>",
        "",
        f"📝 <b>Request</b>\n<blockquote>{escape(request_text)}</blockquote>",
        "",
        f"🤖 <b>Answer</b>\n<blockquote>{escape(answer)}</blockquote>",
    ]
    if tool_calls:
        parts.extend(
            [
                "",
                f"🔧 <b>Observed Tool Calls</b>\n<blockquote>{escape(render_observed_tool_calls(tool_calls))}</blockquote>",
            ]
        )
    return "\n".join(parts)


def process_tool(chat_id: int, request_text: str) -> str:
    request_text = request_text.strip()
    if not request_text:
        return "⚠️ Отправь <code>/tool твой запрос</code>."

    contexts = retrieve_context(request_text)
    run_id = f"{RUN_PREFIX}-tool-{chat_id}-{int(time.time())}"
    if LLM_BACKEND == "nullclaw":
        tool_prompt = (
            "You are a helpful assistant running through nullclaw. "
            "Use available tools when helpful instead of guessing. "
            "Do not invent file contents, command output, paths, or tool results. "
            "If a tool fails, say so plainly.\n\n"
            f"User request: {request_text}"
        )
        grounding_context = [request_text]
        rendered_context = request_text
    else:
        tool_prompt = (
            "You are a helpful assistant. Use the search_docs tool when helpful. "
            f"User request: {request_text}"
        )
        grounding_context = [request_text, *contexts]
        rendered_context = format_context(contexts)

    with client.span(run_id, "llm.call", source="telegram-bot", model=model_label()) as span:
        response = invoke_model(
            run_id,
            [{"role": "user", "content": tool_prompt}],
            tools=TOOLS_SCHEMA if LLM_BACKEND == "ollama" else TOOLS_SCHEMA,
        )
        span.input_tokens = response.get("prompt_eval_count")
        span.output_tokens = response.get("eval_count")

    message = response.get("message", {})
    tool_calls = message.get("tool_calls", [])
    if not tool_calls:
        notes = message.get("content", "").strip() or "Model returned no tool call."
        empty_eval = (
            build_nullclaw_tool_eval(run_id, [])
            if LLM_BACKEND == "nullclaw"
            else ToolCallScorer(tools=TOOLS_SCHEMA).score(run_id=run_id)
        )
        client.ingest_eval(empty_eval)
        return (
            "🛠️ <b>Tool Check</b>\n"
            f"Status: <b>{verdict_badge('fail')}</b>\n\n"
            "Модель не вернула tool call.\n\n"
            f"📄 <b>Raw response</b>\n<blockquote>{escape(notes)}</blockquote>"
        )

    schema_eval = (
        build_nullclaw_tool_eval(
            run_id,
            tool_calls,
            observed_run_id=response.get("observed_run_id"),
            observed_source=response.get("observed_source"),
            observed_span_count=as_int(response.get("observed_span_count")),
        )
        if LLM_BACKEND == "nullclaw"
        else ToolCallScorer(tools=TOOLS_SCHEMA).score(run_id=run_id, tool_calls=tool_calls)
    )
    grounding_eval = ToolCallGroundingScorer(
        context=grounding_context,
        backend=TOOL_GROUNDING_BACKEND,
        llm_url=TOOL_GROUNDING_LLM_URL,
        llm_model=TOOL_GROUNDING_MODEL,
    ).score(
        run_id=run_id,
        tool_calls=tool_calls,
    )
    client.ingest_eval(schema_eval)
    client.ingest_eval(grounding_eval)

    return (
        "🛠️ <b>Tool Check</b>\n"
        f"Schema: <b>{verdict_badge(schema_eval.verdict)}</b> <code>{schema_eval.score:.3f}</code>\n"
        f"Grounding: <b>{verdict_badge(grounding_eval.verdict)}</b> <code>{grounding_eval.score:.3f}</code>\n\n"
        f"📝 <b>Request</b>\n<blockquote>{escape(request_text)}</blockquote>\n\n"
        f"🔧 <b>Tool Calls</b>\n<blockquote>{escape(render_observed_tool_calls(tool_calls))}</blockquote>\n\n"
        f"📐 <b>Schema notes</b>\n<blockquote>{escape(schema_eval.notes or '')}</blockquote>\n\n"
        f"🧭 <b>Grounding notes</b>\n<blockquote>{escape(grounding_eval.notes or '')}</blockquote>\n\n"
        f"📚 <b>Context</b>\n<blockquote>{escape(rendered_context)}</blockquote>"
    )


def process_status() -> str:
    ollama_ok = True
    try:
        with urllib.request.urlopen(f"{OLLAMA_URL}/api/tags", timeout=5) as resp:
            ollama_ok = resp.status == 200
    except Exception:
        ollama_ok = False

    nullwatch_ok = client.is_alive()
    nullclaw_ok = nullclaw.is_alive()
    return (
        "📊 <b>Status</b>\n"
        f"Backend: <code>{escape(LLM_BACKEND)}</code>\n"
        f"Ollama: <b>{status_badge(ollama_ok)}</b>\n"
        f"nullclaw: <b>{status_badge(nullclaw_ok)}</b>\n"
        f"Model: <code>{escape(model_label())}</code>\n"
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


@router.message(Command("agent"))
async def agent_handler(message: Message) -> None:
    command, payload = parse_command(message.text or "")
    del command
    reply = await run_blocking(message, process_agent, message.chat.id, payload)
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
    processor = process_agent if LLM_BACKEND == "nullclaw" else process_rag
    reply = await run_blocking(message, processor, message.chat.id, message.text)
    await message.answer(reply, parse_mode=ParseMode.HTML, disable_web_page_preview=True)


async def amain() -> int:
    bot = Bot(BOT_TOKEN)
    dp = Dispatcher()
    dp.include_router(router)

    print(f"🤖 Starting {BOT_NAME} with backend {LLM_BACKEND} and model {model_label()}")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)
    return 0


def main() -> int:
    try:
        return asyncio.run(amain())
    except NullclawGatewayError as exc:
        print(f"nullclaw error: {exc}")
        return 1
    except KeyboardInterrupt:
        print("Bot stopped")
        return 0
