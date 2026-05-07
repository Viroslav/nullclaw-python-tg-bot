import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("BOT_TOKEN", "test-token")
os.environ.setdefault("LLM_BACKEND", "nullclaw")

from nullclaw_python_tg_bot import bot  # noqa: E402


class SplitPrefixedCommandTests(unittest.TestCase):
    def test_agent_prefix_is_split(self):
        command, payload = bot.split_prefixed_command("/agent remember me")
        self.assertEqual(command, "agent")
        self.assertEqual(payload, "remember me")

    def test_plain_text_is_not_treated_as_prefixed_command(self):
        command, payload = bot.split_prefixed_command("remember me")
        self.assertIsNone(command)
        self.assertEqual(payload, "remember me")


class BotCommandsTests(unittest.TestCase):
    def test_registered_bot_commands_match_handlers(self):
        commands = bot.build_bot_commands()
        pairs = [(item.command, item.description) for item in commands]

        self.assertEqual(
            pairs,
            [
                ("start", "Start bot and show help"),
                ("help", "Show available commands"),
                ("status", "Check services and model status"),
                ("agent", "Send request to nullclaw agent"),
                ("rag", "Run RAG answer with hallucination check"),
                ("tool", "Run tool-calling evaluation"),
                ("show_md", "Show a workspace markdown file"),
                ("set_md", "Replace a workspace markdown file"),
                ("set_identity", "Update IDENTITY.md deterministically"),
            ],
        )


class WorkspaceMarkdownTests(unittest.TestCase):
    def test_resolve_workspace_markdown_rejects_paths_outside_whitelist(self):
        with self.assertRaises(ValueError):
            bot.resolve_workspace_markdown("../IDENTITY.md")

    def test_process_set_md_writes_whitelisted_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(bot, "WORKSPACE_DIR", Path(tmp)):
                reply = bot.process_set_md("TOOLS.md\n# TOOLS\ncalculator is preferred")

                self.assertIn("Updated", reply)
                content = Path(tmp, "TOOLS.md").read_text(encoding="utf-8")
                self.assertIn("calculator is preferred", content)

    def test_process_show_md_reads_whitelisted_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp, "IDENTITY.md")
            path.write_text("# Identity\nhello\n", encoding="utf-8")
            with patch.object(bot, "WORKSPACE_DIR", Path(tmp)):
                reply = bot.process_show_md("IDENTITY.md")

                self.assertIn("IDENTITY.md", reply)
                self.assertIn("hello", reply)

    def test_process_set_identity_writes_identity_markdown(self):
        payload = (
            'Name: Nullclaw AI moderation team\n'
            "Creature: AI assistant\n"
            "Vibe: warm\n"
            "Emoji: ☺️\n"
            "Avatar:\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(bot, "WORKSPACE_DIR", Path(tmp)):
                reply = bot.process_set_identity(payload)
                content = Path(tmp, "IDENTITY.md").read_text(encoding="utf-8")

                self.assertIn("Updated", reply)
                self.assertIn("Nullclaw AI moderation team", content)
                self.assertIn("AI assistant", content)


class ProcessorRoutingTests(unittest.TestCase):
    def test_process_rag_reroutes_agent_command(self):
        with patch.object(bot, "process_agent", return_value="agent-result") as mock_agent:
            result = bot.process_rag(1, "/agent remember that my name is Nikolay")

        self.assertEqual(result, "agent-result")
        mock_agent.assert_called_once_with(1, "remember that my name is Nikolay")

    def test_process_tool_reroutes_rag_command(self):
        with patch.object(bot, "process_rag", return_value="rag-result") as mock_rag:
            result = bot.process_tool(1, "/rag who created zig?")

        self.assertEqual(result, "rag-result")
        mock_rag.assert_called_once_with(1, "who created zig?")


class NullclawToolEvalTests(unittest.TestCase):
    def test_runtime_errors_are_reported_when_no_tool_call_was_observed(self):
        eval_ = bot.build_nullclaw_tool_eval(
            "run-1",
            [],
            observed_run_id="run-observed",
            observed_source="nullclaw-hackathon-test",
            observed_errors=["llm.response: AllProvidersFailed"],
        )

        self.assertEqual(eval_.verdict, "fail")
        self.assertIn("AllProvidersFailed", eval_.notes)


class RuntimeErrorReplyTests(unittest.TestCase):
    def test_timeout_reply_mentions_timeout_env(self):
        reply = bot.build_runtime_error_reply(TimeoutError("timed out"))

        self.assertIn("Request Timed Out", reply)
        self.assertIn("NULLCLAW_TIMEOUT_SECS", reply)

    def test_gateway_reply_renders_detail(self):
        reply = bot.build_runtime_error_reply(
            bot.NullclawGatewayError("nullclaw gateway POST /a2a failed with 401: unauthorized")
        )

        self.assertIn("Gateway Error", reply)
        self.assertIn("401", reply)


class RunIdTests(unittest.TestCase):
    def test_build_run_id_contains_kind_and_chat(self):
        run_id = bot.build_run_id("agent", 42)

        self.assertIn("agent", run_id)
        self.assertIn("42", run_id)


class RunCorrelationTests(unittest.TestCase):
    def test_request_hint_wins_over_newer_unrelated_run(self):
        matching_run = [
            {
                "run_id": "run-match",
                "source": "nullclaw-hackathon-test",
                "operation": "llm.request",
                "stored_at_ms": 110,
                "attributes_json": '[{"key":"detail","value":{"stringValue":"#2 role=user content=\\"please read TOOLS.md\\""}}]',
            },
            {
                "run_id": "run-match",
                "source": "nullclaw-hackathon-test",
                "operation": "turn.complete",
                "stored_at_ms": 130,
                "attributes_json": "[]",
            },
        ]
        newer_unrelated_run = [
            {
                "run_id": "run-newer",
                "source": "nullclaw-hackathon-test",
                "operation": "llm.request",
                "stored_at_ms": 120,
                "attributes_json": '[{"key":"detail","value":{"stringValue":"#2 role=user content=\\"unrelated request\\""}}]',
            },
            {
                "run_id": "run-newer",
                "source": "nullclaw-hackathon-test",
                "operation": "turn.complete",
                "stored_at_ms": 140,
                "attributes_json": "[]",
            },
        ]

        with patch.object(
            bot.client,
            "list_spans",
            return_value=[*matching_run, *newer_unrelated_run],
        ):
            run_id, source, spans = bot.find_recent_nullclaw_run(100, request_hint="please read TOOLS.md")

        self.assertEqual(run_id, "run-match")
        self.assertEqual(source, "nullclaw-hackathon-test")
        self.assertEqual([span["run_id"] for span in spans], ["run-match", "run-match"])

    def test_extract_nullclaw_tool_calls_uses_matching_run(self):
        spans = [
            {
                "run_id": "run-match",
                "source": "nullclaw-hackathon-test",
                "operation": "llm.request",
                "stored_at_ms": 110,
                "attributes_json": '[{"key":"detail","value":{"stringValue":"#2 role=user content=\\"please read TOOLS.md\\""}}]',
            },
            {
                "run_id": "run-match",
                "source": "nullclaw-hackathon-test",
                "operation": "tool.call",
                "stored_at_ms": 120,
                "tool_name": "file_read",
                "status": "ok",
                "attributes_json": '[{"key":"args","value":{"stringValue":"\\"{\\\\\\"path\\\\\\":\\\\\\"TOOLS.md\\\\\\"}\\""}},{"key":"detail","value":{"stringValue":"ok"}}]',
            },
            {
                "run_id": "run-other",
                "source": "nullclaw-hackathon-test",
                "operation": "tool.call",
                "stored_at_ms": 130,
                "tool_name": "file_read",
                "status": "ok",
                "attributes_json": '[{"key":"args","value":{"stringValue":"\\"{\\\\\\"path\\\\\\":\\\\\\"OTHER.md\\\\\\"}\\""}}]',
            },
        ]

        with patch.object(bot.client, "list_spans", return_value=spans):
            tool_calls, observed = bot.extract_nullclaw_tool_calls(100, request_hint="please read TOOLS.md")

        self.assertEqual(observed["observed_run_id"], "run-match")
        self.assertEqual(tool_calls[0]["name"], "file_read")
        self.assertEqual(tool_calls[0]["arguments"], {"path": "TOOLS.md"})


if __name__ == "__main__":
    unittest.main()
