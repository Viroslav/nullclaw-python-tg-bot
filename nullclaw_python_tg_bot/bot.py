import asyncio
import html
import json
import os
import re
import sys
import time
import traceback
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
from aiogram.types import BotCommand, Message  # noqa: E402
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
NULLCLAW_TIMEOUT_SECS = int(os.getenv("NULLCLAW_TIMEOUT_SECS", "180"))
NULLCLAW_FOLLOWUP_TIMEOUT_SECS = int(os.getenv("NULLCLAW_FOLLOWUP_TIMEOUT_SECS", "600"))
NULLCLAW_FOLLOWUP_POLL_SECS = float(os.getenv("NULLCLAW_FOLLOWUP_POLL_SECS", "3"))
TOOL_GROUNDING_BACKEND = (
    os.getenv("TOOL_GROUNDING_BACKEND", "llm" if LLM_BACKEND == "nullclaw" else "keyword").strip().lower()
    or ("llm" if LLM_BACKEND == "nullclaw" else "keyword")
)
TOOL_GROUNDING_LLM_URL = os.getenv("TOOL_GROUNDING_LLM_URL", f"{OLLAMA_URL}/v1").rstrip("/")
TOOL_GROUNDING_MODEL = os.getenv("TOOL_GROUNDING_MODEL", OLLAMA_MODEL).strip() or OLLAMA_MODEL
NULLCLAW_HOME_DIR = Path(
    os.getenv(
        "NULLCLAW_HOME",
        str(Path(__file__).resolve().parents[2] / "nullclaw-test-home"),
    )
).resolve()
WORKSPACE_DIR = Path(
    os.getenv(
        "NULLCLAW_WORKSPACE_DIR",
        str(NULLCLAW_HOME_DIR / "workspace"),
    )
).resolve()

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
ALLOWED_WORKSPACE_MARKDOWN_FILES = {
    "AGENTS.md",
    "SOUL.md",
    "TOOLS.md",
    "CONFIG.md",
    "IDENTITY.md",
    "USER.md",
    "HEARTBEAT.md",
    "BOOTSTRAP.md",
    "MEMORY.md",
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
    timeout=NULLCLAW_TIMEOUT_SECS,
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


def build_run_id(kind: str, chat_id: int) -> str:
    return f"{RUN_PREFIX}-{kind}-{chat_id}-{int(time.time())}"


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


def summarize_nullclaw_spans(spans: list[dict]) -> dict:
    operations: list[str] = []
    errors: list[str] = []

    for span in spans:
        operation = str(span.get("operation") or "")
        status = str(span.get("status") or "")
        if operation and operation not in operations:
            operations.append(operation)
        if status != "error":
            continue

        error_message = str(span.get("error_message") or "").strip()
        if error_message:
            errors.append(f"{operation}: {error_message}" if operation else error_message)
        elif operation:
            errors.append(f"{operation}: status=error")
        else:
            errors.append("runtime span reported status=error")

    return {
        "operations": operations,
        "errors": errors,
    }


def normalize_match_text(value: str) -> str:
    return " ".join(value.split()).strip().lower()


def span_detail_text(span: dict) -> str:
    detail_parts = []

    error_message = span.get("error_message")
    if isinstance(error_message, str) and error_message.strip():
        detail_parts.append(error_message)

    attributes = parse_attributes_json(span.get("attributes_json"))
    detail = parse_tool_detail(attributes)
    if detail:
        detail_parts.append(detail)

    return "\n".join(detail_parts)


def score_run_candidate(spans: list[dict], request_hint: str = "") -> tuple[int, int, int, int, int]:
    normalized_hint = normalize_match_text(request_hint) if request_hint else ""
    latest_ms = max(as_int(span.get("stored_at_ms")) for span in spans)
    completed = any(span.get("operation") == "turn.complete" for span in spans)
    tool_calls = sum(1 for span in spans if span.get("operation") == "tool.call")
    span_count = len(spans)

    request_match = 0
    if normalized_hint:
        for span in spans:
            if span.get("operation") != "llm.request":
                continue
            detail = normalize_match_text(span_detail_text(span))
            if normalized_hint and normalized_hint in detail:
                request_match = 1
                break

    return (request_match, int(completed), tool_calls, latest_ms, span_count)


def find_recent_nullclaw_run(
    started_at_ms: int,
    request_hint: str = "",
) -> tuple[str | None, str | None, list[dict]]:
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
                key=lambda key: score_run_candidate(grouped[key], request_hint=request_hint),
            )
            best_spans = sorted(grouped[best_key], key=lambda span: as_int(span.get("stored_at_ms")))
            return best_key[1], best_key[0], best_spans
        time.sleep(NULLCLAW_SPAN_POLL_DELAY_SECS)
    return None, None, []


def extract_nullclaw_tool_calls(started_at_ms: int, request_hint: str = "") -> tuple[list[dict], dict]:
    observed_run_id, observed_source, spans = find_recent_nullclaw_run(
        started_at_ms,
        request_hint=request_hint,
    )
    tool_calls: list[dict] = []
    span_summary = summarize_nullclaw_spans(spans)

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
        "observed_operations": span_summary["operations"],
        "observed_errors": span_summary["errors"],
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
    tool_calls, observed = extract_nullclaw_tool_calls(started_at_ms, request_hint=prompt)
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
    observed_operations: list[str] | None = None,
    observed_errors: list[str] | None = None,
) -> Eval:
    base_meta = {
        "backend": "nullclaw",
        "observed_run_id": observed_run_id,
        "observed_source": observed_source,
        "observed_span_count": observed_span_count,
        "observed_operations": observed_operations or [],
        "observed_errors": observed_errors or [],
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
        if observed_errors:
            return Eval(
                run_id=run_id,
                eval_key="tool_call_validity",
                scorer="nullclaw-observed-tools",
                score=0.0,
                verdict="fail",
                notes="Observed nullclaw runtime errors before any tool.call span: " + "; ".join(observed_errors),
                meta={**base_meta, "tool_count": 0},
            )

        rendered_operations = ", ".join(observed_operations or [])
        return Eval(
            run_id=run_id,
            eval_key="tool_call_validity",
            scorer="nullclaw-observed-tools",
            score=0.0,
            verdict="fail",
            notes=(
                "Observed the nullclaw run in nullwatch, but there were no tool.call spans for this request. "
                + (
                    f"Observed operations: {rendered_operations}. "
                    if rendered_operations
                    else ""
                )
                + "The model likely answered directly without executing tools."
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


def split_prefixed_command(text: str) -> tuple[str | None, str]:
    stripped = text.strip()
    if not stripped:
        return None, ""

    for command in ("agent", "rag", "tool"):
        prefix = f"/{command}"
        if stripped == prefix:
            return command, ""
        if stripped.startswith(prefix + " "):
            return command, stripped[len(prefix) + 1 :].strip()

    return None, stripped


def resolve_workspace_markdown(filename: str) -> Path:
    normalized = filename.strip()
    if "/" in normalized or "\\" in normalized:
        raise ValueError("Only workspace root markdown files are allowed.")
    if normalized not in ALLOWED_WORKSPACE_MARKDOWN_FILES:
        allowed = ", ".join(sorted(ALLOWED_WORKSPACE_MARKDOWN_FILES))
        raise ValueError(f"Unsupported markdown file. Allowed: {allowed}")
    return WORKSPACE_DIR / normalized


def read_workspace_markdown(filename: str) -> str:
    path = resolve_workspace_markdown(filename)
    return path.read_text(encoding="utf-8")


def write_workspace_markdown(filename: str, content: str) -> Path:
    path = resolve_workspace_markdown(filename)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content.rstrip() + "\n", encoding="utf-8")
    return path


def parse_set_md_payload(payload: str) -> tuple[str, str]:
    stripped = payload.strip()
    if not stripped:
        raise ValueError("Usage: /set_md <FILE.md> followed by the full markdown content.")

    parts = stripped.split(None, 1)
    if len(parts) < 2:
        raise ValueError("Usage: /set_md <FILE.md> followed by the full markdown content.")
    filename, content = parts[0], parts[1].strip()
    if not content:
        raise ValueError("Markdown content must not be empty.")
    return filename, content


def parse_identity_payload(payload: str) -> dict[str, str]:
    fields = {
        "name": "",
        "creature": "",
        "vibe": "",
        "emoji": "",
        "avatar": "",
    }
    aliases = {
        "name": "name",
        "creature": "creature",
        "vibe": "vibe",
        "emoji": "emoji",
        "avatar": "avatar",
    }

    for raw_line in payload.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        line = re.sub(r"^\d+\.\s*", "", line)
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        normalized_key = key.strip().lower()
        field_name = aliases.get(normalized_key)
        if field_name:
            fields[field_name] = value.strip().strip('"')

    return fields


def build_identity_markdown(fields: dict[str, str]) -> str:
    return (
        "# IDENTITY.md - Who Am I?\n\n"
        "_Workspace identity for this nullclaw runtime._\n\n"
        f"- **Name:** {fields['name']}\n"
        f"- **Creature:** {fields['creature']}\n"
        f"- **Vibe:** {fields['vibe']}\n"
        f"- **Emoji:** {fields['emoji']}\n"
        f"- **Avatar:** {fields['avatar']}\n\n"
        "---\n\n"
        "This file defines the runtime identity that nullclaw injects into the system prompt.\n"
    )


def build_help() -> str:
    return (
        f"🤖 <b>{escape(BOT_NAME)}</b>\n"
        f"Backend: <code>{escape(LLM_BACKEND)}</code>\n"
        f"Model: <code>{escape(model_label())}</code>\n\n"
        "Доступные команды:\n"
        "• <code>/agent &lt;message&gt;</code> — прямой запрос в nullclaw для memory/skills/tools\n"
        "• <code>/rag &lt;question&gt;</code> — ответ + проверка RAG hallucination\n"
        "• <code>/tool &lt;request&gt;</code> — tool call + schema/grounding checks\n"
        "• <code>/show_md &lt;FILE.md&gt;</code> — показать workspace markdown файл\n"
        "• <code>/set_md &lt;FILE.md&gt; ...</code> — детерминированно перезаписать workspace markdown файл\n"
        "• <code>/set_identity ...</code> — обновить IDENTITY.md без участия модели\n"
        "• <code>/status</code> — состояние сервисов\n"
        "• <code>/help</code> — подсказка\n\n"
        "💡 В режиме <code>nullclaw</code> обычный текст идет как <code>/agent</code>. "
        "В режиме <code>ollama</code> обычный текст считается как <code>/rag</code>."
    )


def build_bot_commands() -> list[BotCommand]:
    return [
        BotCommand(command="start", description="Start bot and show help"),
        BotCommand(command="help", description="Show available commands"),
        BotCommand(command="status", description="Check services and model status"),
        BotCommand(command="agent", description="Send request to nullclaw agent"),
        BotCommand(command="rag", description="Run RAG answer with hallucination check"),
        BotCommand(command="tool", description="Run tool-calling evaluation"),
        BotCommand(command="show_md", description="Show a workspace markdown file"),
        BotCommand(command="set_md", description="Replace a workspace markdown file"),
        BotCommand(command="set_identity", description="Update IDENTITY.md deterministically"),
    ]


def process_show_md(filename: str) -> str:
    filename = filename.strip()
    if not filename:
        allowed = ", ".join(sorted(ALLOWED_WORKSPACE_MARKDOWN_FILES))
        return f"⚠️ Usage: <code>/show_md FILE.md</code>\nAllowed: <code>{escape(allowed)}</code>"
    try:
        content = read_workspace_markdown(filename)
    except FileNotFoundError:
        return f"⚠️ File <code>{escape(filename)}</code> not found in workspace."
    except ValueError as exc:
        return f"⚠️ {escape(str(exc))}"

    return (
        f"📄 <b>{escape(filename)}</b>\n"
        f"<blockquote>{escape(content)}</blockquote>"
    )


def process_set_md(payload: str) -> str:
    try:
        filename, content = parse_set_md_payload(payload)
        path = write_workspace_markdown(filename, content)
    except ValueError as exc:
        return f"⚠️ {escape(str(exc))}"

    return (
        f"✅ Updated <code>{escape(path.name)}</code>\n\n"
        f"<blockquote>{escape(content)}</blockquote>"
    )


def process_set_identity(payload: str) -> str:
    if not payload.strip():
        return (
            "⚠️ Usage:\n"
            "<blockquote>/set_identity\n"
            "Name: Nullclaw AI moderation team\n"
            "Creature: AI assistant\n"
            "Vibe: warm\n"
            "Emoji: ☺️\n"
            "Avatar:</blockquote>"
        )

    fields = parse_identity_payload(payload)
    if not fields["name"]:
        return "⚠️ <code>Name:</code> is required for <code>/set_identity</code>."

    content = build_identity_markdown(fields)
    write_workspace_markdown("IDENTITY.md", content)
    return (
        "✅ Updated <code>IDENTITY.md</code>\n\n"
        f"<blockquote>{escape(content)}</blockquote>"
    )


def process_rag(chat_id: int, question: str) -> str:
    question = question.strip()
    routed_command, routed_payload = split_prefixed_command(question)
    if routed_command == "agent":
        return process_agent(chat_id, routed_payload)
    if routed_command == "tool":
        return process_tool(chat_id, routed_payload)
    if routed_command == "rag":
        question = routed_payload
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


def process_agent_with_run_id(run_id: str, chat_id: int, request_text: str) -> str:
    request_text = request_text.strip()
    routed_command, routed_payload = split_prefixed_command(request_text)
    if routed_command == "rag":
        return process_rag(chat_id, routed_payload)
    if routed_command == "tool":
        return process_tool(chat_id, routed_payload)
    if routed_command == "agent":
        request_text = routed_payload
    if not request_text:
        return "⚠️ Отправь <code>/agent твой запрос</code>."

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


def process_agent(chat_id: int, request_text: str) -> str:
    return process_agent_with_run_id(build_run_id("agent", chat_id), chat_id, request_text)


def latest_nullclaw_task_for_context(context_id: str) -> dict | None:
    tasks = nullclaw.list_tasks(context_id=context_id, history_length=1, page_size=10)
    if not tasks:
        return None

    def sort_key(task: dict) -> str:
        status = task.get("status")
        if isinstance(status, dict):
            timestamp = status.get("timestamp")
            if isinstance(timestamp, str):
                return timestamp
        return ""

    filtered = [task for task in tasks if isinstance(task, dict)]
    if not filtered:
        return None
    return max(filtered, key=sort_key)


async def follow_up_nullclaw_result(
    tg_bot: Bot,
    *,
    chat_id: int,
    message_thread_id: int | None,
    run_id: str,
) -> None:
    deadline = time.time() + NULLCLAW_FOLLOWUP_TIMEOUT_SECS
    while time.time() < deadline:
        try:
            task = await asyncio.to_thread(latest_nullclaw_task_for_context, run_id)
        except Exception as exc:
            print(f"follow-up polling failed for {run_id}: {exc}")
            await asyncio.sleep(NULLCLAW_FOLLOWUP_POLL_SECS)
            continue

        if not task:
            await asyncio.sleep(NULLCLAW_FOLLOWUP_POLL_SECS)
            continue

        status = task.get("status")
        state = ""
        if isinstance(status, dict) and isinstance(status.get("state"), str):
            state = status["state"]

        if state == "completed":
            text = strip_thinking(nullclaw.extract_task_text(task))
            if not text:
                text = "The task completed, but nullclaw returned an empty final message."
            await tg_bot.send_message(
                chat_id,
                "✅ <b>Delayed Result</b>\n\n" + escape(text),
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
                message_thread_id=message_thread_id,
            )
            return

        if state in {"failed", "canceled", "rejected"}:
            await tg_bot.send_message(
                chat_id,
                "⚠️ <b>Delayed Result Failed</b>\n"
                f"<blockquote>Final task state: {escape(state)}</blockquote>",
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
                message_thread_id=message_thread_id,
            )
            return

        await asyncio.sleep(NULLCLAW_FOLLOWUP_POLL_SECS)

    await tg_bot.send_message(
        chat_id,
        "⏳ <b>No Delayed Result Yet</b>\n"
        "The task is still not in a terminal state. Try a shorter prompt or a smaller model.",
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
        message_thread_id=message_thread_id,
    )


def process_tool(chat_id: int, request_text: str) -> str:
    request_text = request_text.strip()
    routed_command, routed_payload = split_prefixed_command(request_text)
    if routed_command == "agent":
        return process_agent(chat_id, routed_payload)
    if routed_command == "rag":
        return process_rag(chat_id, routed_payload)
    if routed_command == "tool":
        request_text = routed_payload
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
            build_nullclaw_tool_eval(
                run_id,
                [],
                observed_run_id=response.get("observed_run_id"),
                observed_source=response.get("observed_source"),
                observed_span_count=as_int(response.get("observed_span_count")),
                observed_operations=response.get("observed_operations"),
                observed_errors=response.get("observed_errors"),
            )
            if LLM_BACKEND == "nullclaw"
            else ToolCallScorer(tools=TOOLS_SCHEMA).score(run_id=run_id)
        )
        client.ingest_eval(empty_eval)
        return (
            "🛠️ <b>Tool Check</b>\n"
            f"Status: <b>{verdict_badge('fail')}</b>\n\n"
            "Модель не вернула tool call.\n\n"
            f"🧭 <b>Diagnostic</b>\n<blockquote>{escape(empty_eval.notes or '')}</blockquote>\n\n"
            f"📄 <b>Raw response</b>\n<blockquote>{escape(notes)}</blockquote>"
        )

    schema_eval = (
        build_nullclaw_tool_eval(
            run_id,
            tool_calls,
            observed_run_id=response.get("observed_run_id"),
            observed_source=response.get("observed_source"),
            observed_span_count=as_int(response.get("observed_span_count")),
            observed_operations=response.get("observed_operations"),
            observed_errors=response.get("observed_errors"),
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


def build_runtime_error_reply(exc: Exception) -> str:
    if isinstance(exc, TimeoutError):
        return (
            "⏳ <b>Request Timed Out</b>\n"
            "The agent did not answer before the bot timeout expired.\n\n"
            f"Current timeout: <code>{NULLCLAW_TIMEOUT_SECS}s</code>\n"
            "Try a shorter prompt, switch to a smaller model, or raise <code>NULLCLAW_TIMEOUT_SECS</code>."
        )

    if isinstance(exc, NullclawGatewayError):
        detail = str(exc)
        if "timed out" in detail.lower():
            return (
                "⏳ <b>Agent Is Still Thinking</b>\n"
                f"<blockquote>{escape(detail)}</blockquote>\n\n"
                "The request reached nullclaw, but the synchronous reply did not arrive in time."
            )
        return (
            "⚠️ <b>Gateway Error</b>\n"
            f"<blockquote>{escape(detail)}</blockquote>"
        )

    return (
        "⚠️ <b>Unexpected Error</b>\n"
        f"<blockquote>{escape(type(exc).__name__)}: {escape(str(exc) or 'no details')}</blockquote>"
    )


async def run_blocking(message: Message, fn, *args) -> str:
    await message.bot.send_chat_action(message.chat.id, ChatAction.TYPING, message_thread_id=message.message_thread_id)
    try:
        return await asyncio.to_thread(fn, *args)
    except Exception as exc:
        print(f"bot handler error in {getattr(fn, '__name__', '<callable>')}: {exc}")
        traceback.print_exc()
        if (
            isinstance(exc, NullclawGatewayError)
            and "timed out" in str(exc).lower()
            and getattr(fn, "__name__", "") == "process_agent_with_run_id"
            and args
        ):
            run_id = str(args[0])
            asyncio.create_task(
                follow_up_nullclaw_result(
                    message.bot,
                    chat_id=message.chat.id,
                    message_thread_id=message.message_thread_id,
                    run_id=run_id,
                )
            )
            return (
                build_runtime_error_reply(exc)
                + "\n\nI will keep polling nullclaw and send a follow-up message if the task completes."
            )
        return build_runtime_error_reply(exc)


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
    run_id = build_run_id("agent", message.chat.id)
    reply = await run_blocking(message, process_agent_with_run_id, run_id, message.chat.id, payload)
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


@router.message(Command("show_md"))
async def show_md_handler(message: Message) -> None:
    text = message.text or ""
    payload = text[len("/show_md") :].strip()
    reply = await run_blocking(message, process_show_md, payload)
    await message.answer(reply, parse_mode=ParseMode.HTML, disable_web_page_preview=True)


@router.message(Command("set_md"))
async def set_md_handler(message: Message) -> None:
    text = message.text or ""
    payload = text[len("/set_md") :].strip()
    reply = await run_blocking(message, process_set_md, payload)
    await message.answer(reply, parse_mode=ParseMode.HTML, disable_web_page_preview=True)


@router.message(Command("set_identity"))
async def set_identity_handler(message: Message) -> None:
    text = message.text or ""
    payload = text[len("/set_identity") :].strip()
    reply = await run_blocking(message, process_set_identity, payload)
    await message.answer(reply, parse_mode=ParseMode.HTML, disable_web_page_preview=True)


@router.message()
async def fallback_handler(message: Message) -> None:
    if not message.text:
        return
    if LLM_BACKEND == "nullclaw":
        run_id = build_run_id("agent", message.chat.id)
        reply = await run_blocking(message, process_agent_with_run_id, run_id, message.chat.id, message.text)
    else:
        reply = await run_blocking(message, process_rag, message.chat.id, message.text)
    await message.answer(reply, parse_mode=ParseMode.HTML, disable_web_page_preview=True)


async def amain() -> int:
    bot = Bot(BOT_TOKEN)
    dp = Dispatcher()
    dp.include_router(router)

    print(f"🤖 Starting {BOT_NAME} with backend {LLM_BACKEND} and model {model_label()}")
    await bot.set_my_commands(build_bot_commands())
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
