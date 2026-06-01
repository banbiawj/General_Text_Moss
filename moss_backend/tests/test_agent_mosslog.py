from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from app.agent import agent_mosslog


class AgentMosslogTests(unittest.TestCase):
    def setUp(self) -> None:
        agent_mosslog.disable_agent_mosslog()

    def tearDown(self) -> None:
        agent_mosslog.disable_agent_mosslog()

    def test_start_agent_mosslog_uses_environment_flag(self) -> None:
        with patch.dict(os.environ, {"MOSSLOG": "1"}, clear=False):
            with patch.object(agent_mosslog, "mossview", return_value="runtime") as mossview:
                runtime = agent_mosslog.start_agent_mosslog(port=8791)

        self.assertEqual(runtime, "runtime")
        mossview.assert_called_once_with(
            host="127.0.0.1",
            port=8791,
            open_browser=False,
            max_events=1000,
        )

    def test_start_agent_mosslog_returns_none_when_disabled(self) -> None:
        with patch.dict(os.environ, {"MOSSLOG": "0"}, clear=False):
            with patch.object(agent_mosslog, "mossview") as mossview:
                runtime = agent_mosslog.start_agent_mosslog()

        self.assertIsNone(runtime)
        mossview.assert_not_called()

    def test_semantic_logs_are_noops_until_enabled(self) -> None:
        with patch.object(agent_mosslog, "mosslog") as mosslog:
            result = agent_mosslog.log_user_input("hello", thread_id="thread-1")

        self.assertIsNone(result)
        mosslog.assert_not_called()

    def test_log_llm_request_uses_consistent_payload(self) -> None:
        agent_mosslog.enable_agent_mosslog()
        prompt = [{"role": "user", "content": "hello"}]

        with patch.object(agent_mosslog, "mosslog", return_value={"id": 1}) as mosslog:
            result = agent_mosslog.log_llm_request(
                "gpt-test",
                prompt,
                raw_payload={"messages": prompt, "tools": []},
                thread_id="thread-1",
                run_id="run-1",
            )

        self.assertEqual(result, {"id": 1})
        mosslog.assert_called_once_with(
            "llm",
            "llm request",
            model="gpt-test",
            prompt=prompt,
            raw_payload={"messages": prompt, "tools": []},
            thread_id="thread-1",
            run_id="run-1",
        )

    def test_log_llm_response_records_response_and_tool_call(self) -> None:
        agent_mosslog.enable_agent_mosslog()

        with patch.object(agent_mosslog, "mosslog") as mosslog:
            agent_mosslog.log_llm_response(
                "gpt-test",
                "answer",
                tool_call=[{"name": "search"}],
                raw_payload={"full_response": {"content": "answer"}},
                duration_ms=12,
            )

        mosslog.assert_called_once_with(
            "llm",
            "llm response",
            model="gpt-test",
            response="answer",
            tool_call=[{"name": "search"}],
            duration_ms=12,
            raw_payload={"full_response": {"content": "answer"}},
        )

    def test_log_tool_result_records_output_and_duration(self) -> None:
        agent_mosslog.enable_agent_mosslog()

        with patch.object(agent_mosslog, "mosslog") as mosslog:
            agent_mosslog.log_tool_result(
                "search_document_blocks",
                [{"id": "moss-block-1"}],
                raw_payload={"args": {"query": "needle"}},
                duration_ms=42,
                conversation_id="conversation-1",
            )

        mosslog.assert_called_once_with(
            "tool",
            "tool result",
            tool="search_document_blocks",
            response=[{"id": "moss-block-1"}],
            duration_ms=42,
            raw_payload={"args": {"query": "needle"}},
            conversation_id="conversation-1",
        )

    def test_log_route_decision_records_task_type_and_reason(self) -> None:
        agent_mosslog.enable_agent_mosslog()

        with patch.object(agent_mosslog, "mosslog") as mosslog:
            agent_mosslog.log_route_decision(
                "local_edit",
                reason="focused block edit",
                raw_payload={"candidate": "local_edit"},
            )

        mosslog.assert_called_once_with(
            "node",
            "intent result",
            task_type="local_edit",
            reason="focused block edit",
            raw_payload={"candidate": "local_edit"},
        )

    def test_log_agent_error_preserves_exception_object(self) -> None:
        agent_mosslog.enable_agent_mosslog()
        error = ValueError("bad output")

        with patch.object(agent_mosslog, "mosslog") as mosslog:
            agent_mosslog.log_agent_error("failed to parse model output", error, request_id="request-1")

        mosslog.assert_called_once_with(
            "error",
            "failed to parse model output",
            error=error,
            request_id="request-1",
        )


if __name__ == "__main__":
    unittest.main()
