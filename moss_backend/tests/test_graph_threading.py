from __future__ import annotations

import shutil
import unittest
import os
from pathlib import Path
from typing import Any, AsyncGenerator
from uuid import uuid4

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage

from app.agent.checkpointing import open_sqlite_checkpointer
from app.core.config import get_settings
from app.agent.graph import (
    _build_execute_messages,
    compile_agent_graph,
    get_conversation_messages,
    stream_agent_events,
)


class FakeCompiledGraph:
    def __init__(self) -> None:
        self.captured_initial_state: dict[str, Any] | None = None
        self.captured_config: dict[str, Any] | None = None

    async def astream_events(
        self,
        initial_state: dict[str, Any],
        *,
        config: dict[str, Any],
        version: str,
    ) -> AsyncGenerator[dict[str, Any], None]:
        self.captured_initial_state = initial_state
        self.captured_config = config
        yield {
            "event": "on_chain_start",
            "name": "intent",
            "data": {},
        }


class FakeReduceCompiledGraph:
    async def astream_events(
        self,
        initial_state: dict[str, Any],
        *,
        config: dict[str, Any],
        version: str,
    ) -> AsyncGenerator[dict[str, Any], None]:
        yield {
            "event": "on_chain_start",
            "name": "reduce",
            "data": {},
        }
        yield {
            "event": "on_chain_end",
            "name": "reduce",
            "data": {
                "output": {
                    "messages": [AIMessage(content="final response")],
                    "pending_mutations": [
                        {
                            "element_id": "moss-block-1",
                            "action_type": "replace",
                            "new_html": "<p id=\"moss-block-1\">updated</p>",
                        }
                    ],
                }
            },
        }


async def drain_events(generator: AsyncGenerator[dict[str, Any], None]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    async for event in generator:
        events.append(event)
    return events


class GraphThreadingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.original_enable_mock_llm = os.environ.get("ENABLE_MOCK_LLM")
        os.environ["ENABLE_MOCK_LLM"] = "true"
        get_settings.cache_clear()

    def tearDown(self) -> None:
        if self.original_enable_mock_llm is None:
            os.environ.pop("ENABLE_MOCK_LLM", None)
        else:
            os.environ["ENABLE_MOCK_LLM"] = self.original_enable_mock_llm
        get_settings.cache_clear()

    def make_temp_dir(self) -> Path:
        temp_dir = Path.cwd() / ".tmp" / "tests" / f"graph-threading-{uuid4().hex}"
        temp_dir.mkdir(parents=True)
        self.addCleanup(lambda: shutil.rmtree(temp_dir, ignore_errors=True))
        return temp_dir

    async def test_stream_agent_events_uses_conversation_id_as_thread_id(self) -> None:
        fake_graph = FakeCompiledGraph()

        await drain_events(
            stream_agent_events(
                session_id="session-a",
                conversation_id="conv-thread123",
                user_input="hello",
                focus_element_id=None,
                focus_block_id=None,
                canvas_snapshot="",
                compiled_graph=fake_graph,
            )
        )

        self.assertEqual(
            fake_graph.captured_config["configurable"],
            {"thread_id": "conv-thread123"},
        )
        self.assertIsNotNone(fake_graph.captured_initial_state)
        messages = fake_graph.captured_initial_state["messages"]
        self.assertEqual(len(messages), 1)
        self.assertIsInstance(messages[0], HumanMessage)
        self.assertEqual(messages[0].content, "hello")
        self.assertEqual(
            fake_graph.captured_initial_state["conversation_id"],
            "conv-thread123",
        )

    async def test_stream_agent_events_sets_recursion_limit_above_langgraph_default(self) -> None:
        fake_graph = FakeCompiledGraph()

        await drain_events(
            stream_agent_events(
                session_id="session-a",
                conversation_id="conv-recursion123",
                user_input="polish the whole document",
                focus_element_id=None,
                focus_block_id=None,
                canvas_snapshot="",
                compiled_graph=fake_graph,
            )
        )

        self.assertIsNotNone(fake_graph.captured_config)
        recursion_limit = fake_graph.captured_config.get("recursion_limit")
        self.assertIsInstance(recursion_limit, int)
        self.assertGreater(recursion_limit, 25)

    async def test_stream_agent_events_publishes_reduce_outputs(self) -> None:
        events = await drain_events(
            stream_agent_events(
                session_id="session-a",
                conversation_id="conv-reduce123",
                user_input="hello",
                focus_element_id=None,
                focus_block_id=None,
                canvas_snapshot="",
                compiled_graph=FakeReduceCompiledGraph(),
            )
        )

        self.assertIn({"event": "node_start", "data": {"node": "reduce"}}, events)
        self.assertIn(
            {
                "event": "chat_chunk",
                "data": {"content": "final response", "done": True},
            },
            events,
        )
        self.assertIn(
            {
                "event": "dom_mutation",
                "data": {
                    "element_id": "moss-block-1",
                    "action_type": "replace",
                    "new_html": "<p id=\"moss-block-1\">updated</p>",
                },
            },
            events,
        )

    async def test_same_conversation_persists_messages_and_different_conversation_isolated(self) -> None:
        temp_dir = self.make_temp_dir()
        db_path = temp_dir / "checkpoints.sqlite3"
        async with open_sqlite_checkpointer(db_path) as saver:
            compiled_graph = compile_agent_graph(checkpointer=saver)
            same_config = {"configurable": {"thread_id": "conv-same1234"}}
            other_config = {"configurable": {"thread_id": "conv-other123"}}

            await drain_events(
                stream_agent_events(
                    session_id="session-a",
                    conversation_id="conv-same1234",
                    user_input="first turn",
                    focus_element_id=None,
                    focus_block_id=None,
                    canvas_snapshot="",
                    compiled_graph=compiled_graph,
                )
            )
            await drain_events(
                stream_agent_events(
                    session_id="session-b",
                    conversation_id="conv-same1234",
                    user_input="second turn",
                    focus_element_id=None,
                    focus_block_id=None,
                    canvas_snapshot="",
                    compiled_graph=compiled_graph,
                )
            )
            await drain_events(
                stream_agent_events(
                    session_id="session-c",
                    conversation_id="conv-other123",
                    user_input="isolated turn",
                    focus_element_id=None,
                    focus_block_id=None,
                    canvas_snapshot="",
                    compiled_graph=compiled_graph,
                )
            )

            same_state = await compiled_graph.aget_state(same_config)
            other_state = await compiled_graph.aget_state(other_config)

        same_contents = [
            str(message.content)
            for message in same_state.values["messages"]
            if isinstance(message, BaseMessage)
        ]
        other_contents = [
            str(message.content)
            for message in other_state.values["messages"]
            if isinstance(message, BaseMessage)
        ]

        self.assertIn("first turn", same_contents)
        self.assertIn("second turn", same_contents)
        self.assertNotIn("isolated turn", same_contents)
        self.assertIn("isolated turn", other_contents)
        self.assertNotIn("first turn", other_contents)

    async def test_get_conversation_messages_returns_frontend_chat_history(self) -> None:
        temp_dir = self.make_temp_dir()
        db_path = temp_dir / "checkpoints.sqlite3"
        async with open_sqlite_checkpointer(db_path) as saver:
            compiled_graph = compile_agent_graph(checkpointer=saver)
            await compiled_graph.aupdate_state(
                {"configurable": {"thread_id": "conv-history123"}},
                {
                    "messages": [
                        HumanMessage(content="first user"),
                        AIMessage(content="first ai"),
                        ToolMessage(content="hidden tool", tool_call_id="tool-1"),
                        HumanMessage(content="second user"),
                    ]
                },
            )

            messages = await get_conversation_messages(
                compiled_graph,
                "conv-history123",
            )

        self.assertEqual(
            messages,
            [
                {"role": "user", "content": "first user"},
                {"role": "ai", "content": "first ai"},
                {"role": "user", "content": "second user"},
            ],
        )

    async def test_get_conversation_messages_returns_empty_for_unknown_thread(self) -> None:
        temp_dir = self.make_temp_dir()
        db_path = temp_dir / "checkpoints.sqlite3"
        async with open_sqlite_checkpointer(db_path) as saver:
            compiled_graph = compile_agent_graph(checkpointer=saver)

            messages = await get_conversation_messages(
                compiled_graph,
                "conv-missing123",
            )

        self.assertEqual(messages, [])

    def test_execute_messages_include_bounded_conversation_history(self) -> None:
        history = [HumanMessage(content=f"history {index}") for index in range(10)]
        task_messages = [HumanMessage(content="task-local")]

        messages = _build_execute_messages(
            system_prompt="system",
            conversation_messages=history,
            task_messages=task_messages,
        )

        contents = [str(message.content) for message in messages]
        self.assertEqual(contents[0], "system")
        self.assertNotIn("history 0", contents)
        self.assertNotIn("history 1", contents)
        self.assertIn("history 2", contents)
        self.assertIn("history 9", contents)
        self.assertEqual(contents[-1], "task-local")


if __name__ == "__main__":
    unittest.main()
