from __future__ import annotations

import os
import unittest
from pathlib import Path
from typing import Any, AsyncGenerator
from unittest.mock import ANY, patch

from langchain_core.messages import AIMessage, HumanMessage

from app.agent import graph as graph_module
from app.agent.graph import (
    _run_tools_for_current_task,
    execute_node,
    execute_task_node,
    intent_node,
    reduce_node,
    stream_agent_events,
)
from app.core.config import get_settings


class FakeTraceGraph:
    async def astream_events(
        self,
        initial_state: dict[str, Any],
        *,
        config: dict[str, Any],
        version: str,
    ) -> AsyncGenerator[dict[str, Any], None]:
        yield {"event": "on_chain_start", "name": "intent", "data": {}}


class FakeChatOpenAI:
    instances: list["FakeChatOpenAI"] = []

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.__class__.instances.append(self)

    def bind_tools(self, tools: list[Any]) -> "FakeChatOpenAI":
        self.tools = tools
        return self

    def invoke(self, messages: list[Any]) -> AIMessage:
        self.messages = messages
        return AIMessage(content="model answer")


class FakeAsyncOnlyChatOpenAI:
    calls: list[dict[str, Any]] = []
    outputs: list[Any] = []

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.schema: type[Any] | None = None
        self.tools: list[Any] = []

    def with_structured_output(self, schema: type[Any], method: str) -> "FakeAsyncOnlyChatOpenAI":
        self.schema = schema
        return self

    def bind_tools(self, tools: list[Any]) -> "FakeAsyncOnlyChatOpenAI":
        self.tools = tools
        return self

    def invoke(self, messages: list[Any]) -> AIMessage:
        raise AssertionError("sync invoke should not be used inside async graph streaming")

    async def ainvoke(self, messages: list[Any]) -> Any:
        self.__class__.calls.append(
            {"schema": self.schema, "tools": self.tools, "messages": messages}
        )
        return self.__class__.outputs.pop(0)


class FakeTool:
    name = "fake_tool"

    def invoke(self, args: dict[str, Any]) -> dict[str, Any]:
        return {"ok": True, "args": args}


async def drain_events(generator: AsyncGenerator[dict[str, Any], None]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    async for event in generator:
        events.append(event)
    return events


class AgentMosslogTraceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.original_enable_mock_llm = os.environ.get("ENABLE_MOCK_LLM")
        self.original_llm_model = os.environ.get("LLM_MODEL")
        os.environ["ENABLE_MOCK_LLM"] = "false"
        os.environ["LLM_MODEL"] = "trace-model"
        get_settings.cache_clear()

    def tearDown(self) -> None:
        if self.original_enable_mock_llm is None:
            os.environ.pop("ENABLE_MOCK_LLM", None)
        else:
            os.environ["ENABLE_MOCK_LLM"] = self.original_enable_mock_llm
        if self.original_llm_model is None:
            os.environ.pop("LLM_MODEL", None)
        else:
            os.environ["LLM_MODEL"] = self.original_llm_model
        get_settings.cache_clear()

    async def test_stream_agent_events_logs_full_request_context(self) -> None:
        with patch.object(graph_module, "log_user_input") as log_user_input:
            await drain_events(
                stream_agent_events(
                    session_id="session-1",
                    conversation_id="conversation-1",
                    user_input="hello",
                    focus_element_id="child-1",
                    focus_block_id="block-1",
                    canvas_snapshot="<p>full document</p>",
                    compiled_graph=FakeTraceGraph(),
                )
            )

        log_user_input.assert_called_once_with(
            "hello",
            raw_payload={
                "session_id": "session-1",
                "conversation_id": "conversation-1",
                "request_id": ANY,
                "focus_element_id": "child-1",
                "focus_block_id": "block-1",
                "canvas_snapshot": "<p>full document</p>",
                "canvas_snapshot_length": 20,
            },
        )

    async def test_intent_node_logs_task_type_and_reason_only(self) -> None:
        os.environ["ENABLE_MOCK_LLM"] = "true"
        get_settings.cache_clear()

        with patch.object(graph_module, "log_route_decision") as log_route_decision:
            output = intent_node(
                {
                    "user_input": "hello",
                    "session_id": "session-1",
                    "conversation_id": "conversation-1",
                    "request_id": "request-1",
                }
            )

        self.assertEqual(output["task_type"], "general_chat")
        log_route_decision.assert_called_once_with(
            "general_chat",
            reason="mock（ENABLE_MOCK_LLM=true，跳过意图识别）",
            raw_payload={
                "output": {
                    "task_type": "general_chat",
                    "task_reason": "mock（ENABLE_MOCK_LLM=true，跳过意图识别）",
                },
                "user_input": "hello",
                "session_id": "session-1",
                "conversation_id": "conversation-1",
                "request_id": "request-1",
            },
        )
        self.assertFalse(hasattr(graph_module, "log_node_enter"))

    async def test_stream_agent_events_uses_async_llm_invocation(self) -> None:
        FakeAsyncOnlyChatOpenAI.calls = []
        FakeAsyncOnlyChatOpenAI.outputs = [
            graph_module.IntentCandidateOutput(
                task_type="general_chat",
                task_reason="clear chat",
            ),
            AIMessage(content="async model answer"),
        ]

        with patch.object(graph_module, "ChatOpenAI", FakeAsyncOnlyChatOpenAI):
            events = await drain_events(
                stream_agent_events(
                    session_id="session-async",
                    conversation_id="conversation-async",
                    user_input="hello",
                    focus_element_id=None,
                    focus_block_id=None,
                    canvas_snapshot="",
                    compiled_graph=graph_module.compile_agent_graph(),
                )
            )

        self.assertEqual(len(FakeAsyncOnlyChatOpenAI.calls), 2)
        self.assertIn(
            {
                "event": "chat_chunk",
                "data": {"content": "async model answer", "done": True},
            },
            events,
        )

    async def test_execute_task_node_logs_full_llm_request_and_response(self) -> None:
        task = {
            "task_id": "task-1",
            "task_prompt": "system prompt",
            "task_message": [HumanMessage(content="task-local instruction")],
            "task_tools": [],
            "status": "pending",
        }
        state = {
            "tasks": [task],
            "current_task_index": 0,
            "conversation_messages": [HumanMessage(content="recent history")],
            "session_id": "session-1",
            "conversation_id": "conversation-1",
            "request_id": "request-1",
        }

        with patch.object(graph_module, "ChatOpenAI", FakeChatOpenAI):
            with patch.object(graph_module, "log_llm_request") as log_llm_request:
                with patch.object(graph_module, "log_llm_response") as log_llm_response:
                    execute_task_node(state)

        request_messages = log_llm_request.call_args.args[1]
        request_kwargs = log_llm_request.call_args.kwargs
        self.assertEqual(log_llm_request.call_args.args[0], "trace-model")
        self.assertEqual(request_messages[0]["role"], "system")
        self.assertEqual(request_messages[0]["content"], "system prompt")
        self.assertEqual(request_messages[1]["content"], "recent history")
        self.assertEqual(request_messages[2]["content"], "task-local instruction")
        self.assertEqual(request_kwargs["raw_payload"]["task"]["task_id"], "task-1")
        self.assertEqual(request_kwargs["raw_payload"]["node"], "execute")

        response_output = log_llm_response.call_args.args[1]
        response_kwargs = log_llm_response.call_args.kwargs
        self.assertEqual(log_llm_response.call_args.args[0], "trace-model")
        self.assertEqual(response_output, "model answer")
        self.assertIsNone(response_kwargs["tool_call"])
        self.assertEqual(response_kwargs["raw_payload"]["full_response"]["content"], "model answer")
        self.assertIn("duration_ms", response_kwargs)

    async def test_execute_task_node_configures_llm_timeout_and_retries(self) -> None:
        task = {
            "task_id": "task-timeout",
            "task_prompt": "system prompt",
            "task_message": [],
            "task_tools": [],
            "status": "pending",
        }
        state = {
            "tasks": [task],
            "current_task_index": 0,
            "conversation_messages": [],
            "session_id": "session-1",
            "conversation_id": "conversation-1",
            "request_id": "request-1",
        }

        FakeChatOpenAI.instances = []
        with patch.object(graph_module, "ChatOpenAI", FakeChatOpenAI):
            execute_task_node(state)

        self.assertEqual(FakeChatOpenAI.instances[0].kwargs["timeout"], 120)
        self.assertEqual(FakeChatOpenAI.instances[0].kwargs["max_retries"], 2)

    async def test_execute_node_configures_llm_timeout_and_retries(self) -> None:
        task = {
            "task_id": "task-timeout",
            "task_prompt": "system prompt",
            "task_message": [],
            "task_tools": [],
            "status": "pending",
        }
        state = {
            "tasks": [task],
            "current_task_index": 0,
            "messages": [],
            "session_id": "session-1",
            "conversation_id": "conversation-1",
            "request_id": "request-1",
        }

        FakeChatOpenAI.instances = []
        with patch.object(graph_module, "ChatOpenAI", FakeChatOpenAI):
            execute_node(state)

        self.assertEqual(FakeChatOpenAI.instances[0].kwargs["timeout"], 120)
        self.assertEqual(FakeChatOpenAI.instances[0].kwargs["max_retries"], 2)

    async def test_send_worker_llm_logs_expose_source_task_index(self) -> None:
        task = {
            "task_id": "task-3",
            "task_prompt": "system prompt",
            "task_message": [],
            "task_tools": [],
            "status": "pending",
        }
        state = {
            "tasks": [task],
            "current_task_index": 0,
            "source_task_index": 2,
            "conversation_messages": [],
            "session_id": "session-1",
            "conversation_id": "conversation-1",
            "request_id": "request-1",
        }

        with patch.object(graph_module, "ChatOpenAI", FakeChatOpenAI):
            with patch.object(graph_module, "log_llm_request") as log_llm_request:
                with patch.object(graph_module, "log_llm_response") as log_llm_response:
                    execute_task_node(state)

        request_kwargs = log_llm_request.call_args.kwargs
        response_kwargs = log_llm_response.call_args.kwargs
        self.assertEqual(request_kwargs["task_index"], 2)
        self.assertEqual(response_kwargs["task_index"], 2)
        self.assertEqual(request_kwargs["raw_payload"]["task_index"], 2)
        self.assertEqual(response_kwargs["raw_payload"]["task_index"], 2)

    async def test_run_tools_logs_only_tool_result_with_raw_payload(self) -> None:
        tool_call = {
            "name": "fake_tool",
            "args": {"query": "needle"},
            "id": "call-1",
        }
        state = {
            "tasks": [
                {
                    "task_id": "task-1",
                    "task_message": [AIMessage(content="", tool_calls=[tool_call])],
                    "tool_budget_usage": {},
                }
            ],
            "current_task_index": 0,
            "source_task_index": 3,
            "session_id": "session-1",
            "conversation_id": "conversation-1",
            "request_id": "request-1",
        }

        with patch.object(graph_module, "DOCUMENT_TOOLS", [FakeTool()]):
            with patch.object(graph_module, "log_tool_result") as log_tool_result:
                _run_tools_for_current_task(state)

        self.assertFalse(hasattr(graph_module, "log_tool_call"))
        log_tool_result.assert_called_once_with(
            "fake_tool",
            {"ok": True, "args": {"query": "needle"}},
            duration_ms=ANY,
            task_index=3,
            raw_payload={
                "tool_call_id": "call-1",
                "args": {"query": "needle"},
                "result": {"ok": True, "args": {"query": "needle"}},
                "session_id": "session-1",
                "conversation_id": "conversation-1",
                "request_id": "request-1",
                "task_id": "task-1",
                "current_task_index": 0,
                "source_task_index": 3,
                "task_index": 3,
            },
        )

    async def test_reduce_node_logs_final_output_only(self) -> None:
        with patch.object(graph_module, "log_node_exit") as log_node_exit:
            output = reduce_node(
                {
                    "request_id": "request-1",
                    "task_results": [
                        {
                            "request_id": "request-1",
                            "task_index": 0,
                            "messages": [AIMessage(content="final answer")],
                            "pending_mutations": [{"element_id": "moss-block-1"}],
                        }
                    ],
                }
            )

        self.assertEqual(output["pending_mutations"], [{"element_id": "moss-block-1"}])
        log_node_exit.assert_called_once_with(
            "reduce",
            {
                "response": [{"role": "assistant", "type": "ai", "content": "final answer"}],
                "mutations": [{"element_id": "moss-block-1"}],
            },
            raw_payload={
                "messages": [{"role": "assistant", "type": "ai", "content": "final answer"}],
                "pending_mutations": [{"element_id": "moss-block-1"}],
                "request_id": "request-1",
            },
        )

    async def test_mosslog_frontend_renders_focused_fields(self) -> None:
        index_html = (
            Path(__file__).parents[1] / "app" / "mosslog" / "static" / "index.html"
        ).read_text(encoding="utf-8")

        self.assertIn("fields.prompt", index_html)
        self.assertIn("fields.response", index_html)
        self.assertIn("fields.task_type", index_html)
        self.assertIn("fields.reason", index_html)
        self.assertIn("fields.mutations", index_html)
        self.assertIn("fields.task_index", index_html)

    async def test_mosslog_frontend_has_task_dropdown_filter(self) -> None:
        index_html = (
            Path(__file__).parents[1] / "app" / "mosslog" / "static" / "index.html"
        ).read_text(encoding="utf-8")

        self.assertIn('id="task-filter"', index_html)
        self.assertIn('value="all"', index_html)
        self.assertIn("activeTaskFilter", index_html)
        self.assertIn("eventTaskIndex", index_html)
        self.assertIn("refreshTaskFilterOptions", index_html)


if __name__ == "__main__":
    unittest.main()
