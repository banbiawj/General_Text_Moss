from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from langchain_core.messages import AIMessage, HumanMessage

from app.agent import graph as graph_module
from app.core.config import get_settings


class FakeIntentChatOpenAI:
    calls: list[dict] = []
    instances: list["FakeIntentChatOpenAI"] = []
    outputs: list[SimpleNamespace] = []

    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.schema = None
        self.__class__.instances.append(self)

    def with_structured_output(self, schema, method: str):
        self.schema = schema
        return self

    def invoke(self, messages):
        self.__class__.calls.append({"schema": self.schema, "messages": messages})
        return self.__class__.outputs.pop(0)


class IntentRoutingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original_enable_mock_llm = os.environ.get("ENABLE_MOCK_LLM")
        self.original_llm_api_key = os.environ.get("LLM_API_KEY")
        os.environ["ENABLE_MOCK_LLM"] = "false"
        os.environ["LLM_API_KEY"] = "test-key"
        get_settings.cache_clear()
        FakeIntentChatOpenAI.calls = []
        FakeIntentChatOpenAI.instances = []
        FakeIntentChatOpenAI.outputs = []

    def tearDown(self) -> None:
        if self.original_enable_mock_llm is None:
            os.environ.pop("ENABLE_MOCK_LLM", None)
        else:
            os.environ["ENABLE_MOCK_LLM"] = self.original_enable_mock_llm
        if self.original_llm_api_key is None:
            os.environ.pop("LLM_API_KEY", None)
        else:
            os.environ["LLM_API_KEY"] = self.original_llm_api_key
        get_settings.cache_clear()

    def test_ambiguous_first_pass_uses_recent_history_for_contextual_intent(self) -> None:
        FakeIntentChatOpenAI.outputs = [
            SimpleNamespace(task_type="ambiguous", task_reason="current input is underspecified"),
            SimpleNamespace(task_type="local_edit", task_reason="recent turns refer to editing the focused paragraph"),
        ]
        history = [
            HumanMessage(content=f"user history {index}")
            if index % 2 == 0
            else AIMessage(content=f"assistant history {index}")
            for index in range(10)
        ]

        with patch.object(graph_module, "ChatOpenAI", FakeIntentChatOpenAI):
            result = graph_module.intent_node(
                {
                    "user_input": "那就改一下",
                    "messages": history + [HumanMessage(content="那就改一下")],
                    "canvas_snapshot": '<p id="moss-block-1">target</p>',
                    "focus_block_id": "moss-block-1",
                    "focus_element_id": "moss-block-1",
                }
            )

        self.assertEqual(result["task_type"], "local_edit")
        self.assertEqual(result["task_reason"], "recent turns refer to editing the focused paragraph")
        self.assertEqual(len(FakeIntentChatOpenAI.calls), 2)
        contextual_messages = FakeIntentChatOpenAI.calls[1]["messages"]
        contextual_payload = str(contextual_messages[-1].content)
        self.assertIn("最近 8 条聊天记录", contextual_payload)
        self.assertNotIn("user history 0", contextual_payload)
        self.assertNotIn("assistant history 1", contextual_payload)
        self.assertIn("user history 2", contextual_payload)
        self.assertIn("assistant history 9", contextual_payload)
        self.assertIn("has_canvas_snapshot: true", contextual_payload)
        self.assertIn("has_focus_block: true", contextual_payload)
        self.assertNotIn("focus_block_id: moss-block-1", contextual_payload)
        self.assertNotIn("moss-block-", contextual_payload)

    def test_clear_first_pass_intent_does_not_use_contextual_intent(self) -> None:
        FakeIntentChatOpenAI.outputs = [
            SimpleNamespace(task_type="document_qa", task_reason="clear document question"),
        ]

        with patch.object(graph_module, "ChatOpenAI", FakeIntentChatOpenAI):
            result = graph_module.intent_node(
                {
                    "user_input": "总结这篇文档",
                    "messages": [HumanMessage(content="总结这篇文档")],
                    "canvas_snapshot": '<p id="moss-block-1">target</p>',
                    "focus_block_id": "moss-block-1",
                }
            )

        self.assertEqual(result["task_type"], "document_qa")
        self.assertEqual(len(FakeIntentChatOpenAI.calls), 1)

    def test_intent_classifier_configures_llm_timeout_and_retries(self) -> None:
        FakeIntentChatOpenAI.outputs = [
            SimpleNamespace(task_type="document_qa", task_reason="clear document question"),
        ]

        with patch.object(graph_module, "ChatOpenAI", FakeIntentChatOpenAI):
            graph_module.intent_node(
                {
                    "user_input": "summarize",
                    "messages": [HumanMessage(content="summarize")],
                    "canvas_snapshot": '<p id="moss-block-1">target</p>',
                    "focus_block_id": "moss-block-1",
                }
            )

        self.assertEqual(FakeIntentChatOpenAI.instances[0].kwargs["timeout"], 120)
        self.assertEqual(FakeIntentChatOpenAI.instances[0].kwargs["max_retries"], 2)


if __name__ == "__main__":
    unittest.main()
