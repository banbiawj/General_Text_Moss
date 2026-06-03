from __future__ import annotations

import asyncio
import os
import unittest
from typing import Any
from unittest.mock import patch

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from app.agent import graph as graph_module
from app.core.config import get_settings


def _task(task_id: str) -> dict:
    return {
        "task_id": task_id,
        "task_message": [],
        "canvas_context": "",
        "canvas_context_blocks": [],
        "canvas_context_operation_seq": 0,
        "task_prompt": f"prompt for {task_id}",
        "task_tools": [],
        "allowed_element_ids": [],
        "status": "pending",
    }


async def _drain_events(generator) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    async for event in generator:
        events.append(event)
    return events


class FakeAsyncChatOpenAI:
    calls: list[dict[str, Any]] = []
    outputs: list[Any] = []

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.schema: type[Any] | None = None
        self.tools: list[Any] = []

    def with_structured_output(self, schema: type[Any], method: str) -> "FakeAsyncChatOpenAI":
        self.schema = schema
        return self

    def bind_tools(self, tools: list[Any]) -> "FakeAsyncChatOpenAI":
        self.tools = tools
        return self

    def invoke(self, messages: list[Any]) -> AIMessage:
        raise AssertionError("streaming graph should use async LLM invocation")

    async def ainvoke(self, messages: list[Any]) -> Any:
        self.__class__.calls.append(
            {"schema": self.schema, "tools": self.tools, "messages": messages}
        )
        return self.__class__.outputs.pop(0)


class GraphSendRoutingTests(unittest.TestCase):
    def test_compiled_graph_uses_send_worker_and_reduce_instead_of_task_advance(self) -> None:
        graph_view = graph_module.graph.get_graph()

        self.assertIn("task_worker", graph_view.nodes)
        self.assertIn("reduce", graph_view.nodes)
        self.assertNotIn("task_advance", graph_view.nodes)

        edge_pairs = {(edge.source, edge.target) for edge in graph_view.edges}
        self.assertIn(("task_assemble", "task_worker"), edge_pairs)
        self.assertIn(("task_worker", "reduce"), edge_pairs)
        self.assertNotIn(("task_assemble", "execute"), edge_pairs)

    def test_route_tasks_returns_send_for_each_task_with_isolated_worker_state(self) -> None:
        route_tasks = getattr(graph_module, "route_tasks", None)
        self.assertTrue(callable(route_tasks), "route_tasks should be defined")
        state = {
            "tasks": [_task("task-1"), _task("task-2")],
            "messages": [HumanMessage(content="hello")],
            "user_input": "hello",
            "canvas_snapshot": '<p id="moss-block-1">text</p>',
            "focus_element_id": "moss-block-1",
            "focus_block_id": "moss-block-1",
            "task_type": "global_edit",
            "task_reason": "test",
            "session_id": "session-1",
            "conversation_id": "conv-1",
            "request_id": "request-1",
        }

        sends = route_tasks(state)

        self.assertEqual(len(sends), 2)
        self.assertTrue(all(isinstance(send, Send) for send in sends))
        self.assertEqual([send.node for send in sends], ["task_worker", "task_worker"])
        self.assertEqual(sends[0].arg["tasks"], [_task("task-1")])
        self.assertEqual(sends[0].arg["current_task_index"], 0)
        self.assertEqual(sends[0].arg["source_task_index"], 0)
        self.assertEqual(sends[0].arg["conversation_messages"], state["messages"])
        self.assertEqual(sends[1].arg["tasks"], [_task("task-2")])
        self.assertEqual(sends[1].arg["source_task_index"], 1)

    def test_parallel_task_workers_do_not_write_tasks_to_parent_state(self) -> None:
        original_enable_mock_llm = os.environ.get("ENABLE_MOCK_LLM")
        os.environ["ENABLE_MOCK_LLM"] = "true"
        get_settings.cache_clear()

        def fanout_node(state: graph_module.AgentState) -> dict:
            return {}

        parent_builder = StateGraph(graph_module.AgentState)
        parent_builder.add_node("fanout", fanout_node)
        parent_builder.add_node("task_worker", graph_module.task_worker_graph)
        parent_builder.add_node("reduce", graph_module.reduce_node)
        parent_builder.add_edge(START, "fanout")
        parent_builder.add_conditional_edges("fanout", graph_module.route_tasks, ["task_worker"])
        parent_builder.add_edge("task_worker", "reduce")
        parent_builder.add_edge("reduce", END)

        try:
            compiled = parent_builder.compile()
            output = asyncio.run(
                compiled.ainvoke(
                    {
                        "tasks": [_task("task-1"), _task("task-2")],
                        "messages": [HumanMessage(content="hello")],
                        "user_input": "hello",
                        "canvas_snapshot": '<p id="moss-block-1">text</p>',
                        "focus_element_id": "moss-block-1",
                        "focus_block_id": "moss-block-1",
                        "task_type": "global_edit",
                        "task_reason": "test",
                        "current_task_index": 0,
                        "pending_mutations": [],
                        "session_id": "session-1",
                        "conversation_id": "conv-1",
                        "request_id": "request-1",
                    }
                )
            )
        finally:
            if original_enable_mock_llm is None:
                os.environ.pop("ENABLE_MOCK_LLM", None)
            else:
                os.environ["ENABLE_MOCK_LLM"] = original_enable_mock_llm
            get_settings.cache_clear()

        self.assertEqual(len(output["messages"]), 3)
        self.assertEqual([task["task_id"] for task in output["tasks"]], ["task-1", "task-2"])

    def test_task_worker_graph_finalizes_outputs_before_parent_reduce(self) -> None:
        graph_view = graph_module.task_worker_graph.get_graph()

        self.assertIn("finalize", graph_view.nodes)

        edge_pairs = {(edge.source, edge.target) for edge in graph_view.edges}
        self.assertIn(("execute", "finalize"), edge_pairs)
        self.assertIn(("finalize", "__end__"), edge_pairs)

    def test_streaming_global_edit_does_not_leak_worker_tasks_to_parent_state(self) -> None:
        original_enable_mock_llm = os.environ.get("ENABLE_MOCK_LLM")
        os.environ["ENABLE_MOCK_LLM"] = "false"
        get_settings.cache_clear()
        text = "\u4e2d" * 1200
        canvas_snapshot = "".join(
            f'<p id="moss-block-{index}">{text}</p>'
            for index in range(20)
        )
        task_count = len(
            graph_module.task_assemble_node(
                {
                    "task_type": "global_edit",
                    "canvas_snapshot": canvas_snapshot,
                    "focus_block_id": None,
                    "focus_element_id": None,
                    "user_input": "polish whole document",
                }
            )["tasks"]
        )
        self.assertGreater(task_count, 1)

        FakeAsyncChatOpenAI.calls = []
        FakeAsyncChatOpenAI.outputs = [
            graph_module.IntentCandidateOutput(
                task_type="global_edit",
                task_reason="whole document edit",
            ),
            *[AIMessage(content=f"chunk {index}") for index in range(task_count)],
        ]

        try:
            with patch.object(graph_module, "ChatOpenAI", FakeAsyncChatOpenAI):
                events = asyncio.run(
                    _drain_events(
                        graph_module.stream_agent_events(
                            session_id="session-1",
                            conversation_id="conversation-1",
                            user_input="polish whole document",
                            focus_element_id=None,
                            focus_block_id=None,
                            canvas_snapshot=canvas_snapshot,
                            compiled_graph=graph_module.compile_agent_graph(),
                        )
                    )
                )
        finally:
            if original_enable_mock_llm is None:
                os.environ.pop("ENABLE_MOCK_LLM", None)
            else:
                os.environ["ENABLE_MOCK_LLM"] = original_enable_mock_llm
            get_settings.cache_clear()

        chunks = [
            event["data"]["content"]
            for event in events
            if event["event"] == "chat_chunk"
        ]
        self.assertEqual(chunks, [f"chunk {index}" for index in range(task_count)])

    def test_reduce_node_orders_task_results_and_publishes_final_outputs(self) -> None:
        reduce_node = getattr(graph_module, "reduce_node", None)
        self.assertTrue(callable(reduce_node), "reduce_node should be defined")
        first_message = AIMessage(content="first result")
        second_message = AIMessage(content="second result")

        result = reduce_node(
            {
                "task_results": [
                    {
                        "task_id": "task-2",
                        "task_index": 1,
                        "messages": [second_message],
                        "pending_mutations": [
                            {"element_id": "moss-block-2", "action_type": "replace", "new_html": "b"}
                        ],
                    },
                    {
                        "task_id": "task-1",
                        "task_index": 0,
                        "messages": [first_message],
                        "pending_mutations": [
                            {"element_id": "moss-block-1", "action_type": "replace", "new_html": "a"}
                        ],
                    },
                ]
            }
        )

        self.assertEqual(result["messages"], [first_message, second_message])
        self.assertEqual(
            result["pending_mutations"],
            [
                {"element_id": "moss-block-1", "action_type": "replace", "new_html": "a"},
                {"element_id": "moss-block-2", "action_type": "replace", "new_html": "b"},
            ],
        )

    def test_reduce_node_ignores_task_results_from_previous_requests(self) -> None:
        reduce_node = getattr(graph_module, "reduce_node", None)
        self.assertTrue(callable(reduce_node), "reduce_node should be defined")
        old_message = AIMessage(content="old result")
        new_message = AIMessage(content="new result")

        result = reduce_node(
            {
                "request_id": "request-new",
                "task_results": [
                    {
                        "task_id": "old-task",
                        "task_index": 0,
                        "request_id": "request-old",
                        "messages": [old_message],
                    },
                    {
                        "task_id": "new-task",
                        "task_index": 0,
                        "request_id": "request-new",
                        "messages": [new_message],
                    },
                ],
            }
        )

        self.assertEqual(result["messages"], [new_message])


if __name__ == "__main__":
    unittest.main()
