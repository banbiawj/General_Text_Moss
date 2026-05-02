from __future__ import annotations

import unittest

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.agent.document_context import (
    build_context_update,
    classify_intent,
    mark_global_batch_processed,
    search_document_blocks,
    update_authorization_after_retrieval,
    validate_canvas_mutation,
)
from app.agent.graph import _visible_conversation_messages
from app.agent.prompt import build_system_prompt


class AgentRefactorTests(unittest.TestCase):
    def test_intent_classification(self) -> None:
        self.assertEqual(classify_intent("你好")[0], "general_chat")
        self.assertEqual(classify_intent("总结项目经历")[0], "document_qa")
        self.assertEqual(classify_intent("润色这段", "moss-block-1")[0], "local_edit")
        self.assertEqual(classify_intent("全文统一风格")[0], "global_edit")

    def test_document_qa_context_does_not_include_full_snapshot(self) -> None:
        snapshot = (
            '<h1 id="moss-block-1">标题</h1>'
            '<p id="moss-block-2">项目经历 Python 后端</p>'
            '<p id="moss-block-3">教育经历</p>'
        )
        state = {
            "messages": [HumanMessage(content="总结项目经历")],
            "canvas_snapshot": snapshot,
            "focus_element_id": "moss-block-2",
            "focus_block_id": "moss-block-2",
            "intent": "document_qa",
            "intent_reason": "test",
            "retrieved_block_ids": [],
        }

        context_update = build_context_update(state)
        prompt = build_system_prompt(
            intent="document_qa",
            intent_reason="test",
            canvas_context=context_update["canvas_context"],
            allowed_element_ids=context_update["allowed_element_ids"],
        )

        self.assertNotIn(snapshot, prompt)
        self.assertNotIn("canvas_snapshot", prompt)
        self.assertIn("Document outline", prompt)
        self.assertIn("Focus block brief", prompt)

    def test_search_authorizes_returned_block_ids_only(self) -> None:
        snapshot = (
            '<p id="moss-block-1">项目经历 Python 后端</p>'
            '<p id="moss-block-2">教育经历</p>'
            '<p id="moss-block-3">技能栈</p>'
        )
        state = {
            "canvas_snapshot": snapshot,
            "intent": "document_qa",
            "intent_reason": "test",
            "retrieved_block_ids": [],
        }
        context_update = build_context_update(state)
        result = search_document_blocks({**state, **context_update}, "Python", 5)
        auth_update = update_authorization_after_retrieval({**state, **context_update}, result, "Python", 5)

        self.assertEqual([block["block_id"] for block in result["blocks"]], ["moss-block-1"])
        self.assertEqual(auth_update["retrieved_block_ids"], ["moss-block-1"])
        self.assertEqual(auth_update["allowed_element_ids"], ["moss-block-1"])

    def test_mutation_validation_requires_authorization_and_target_id(self) -> None:
        state = {"allowed_element_ids": ["moss-block-1"]}

        self.assertIsNone(
            validate_canvas_mutation(
                state,
                element_id="moss-block-1",
                action_type="replace",
                new_html='<p id="moss-block-1">ok</p>',
            )
        )
        self.assertIn(
            "preserve target id",
            validate_canvas_mutation(
                state,
                element_id="moss-block-1",
                action_type="replace",
                new_html="<p>missing id</p>",
            ),
        )
        self.assertIn(
            "not authorized",
            validate_canvas_mutation(
                state,
                element_id="moss-block-2",
                action_type="delete",
                new_html="",
            ),
        )

    def test_global_batch_window_drops_previous_batch_tool_history(self) -> None:
        snapshot = "".join(f'<p id="moss-block-{index}">block {index}</p>' for index in range(3))
        state = {
            "messages": [HumanMessage(content="全文润色")],
            "canvas_snapshot": snapshot,
            "intent": "global_edit",
            "intent_reason": "test",
            "retrieved_block_ids": [],
        }
        first_context = build_context_update(state)
        self.assertEqual(first_context["current_batch_ids"], ["moss-block-0"])

        messages = [
            HumanMessage(content="全文润色"),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "update_canvas_element",
                        "args": {
                            "element_id": "moss-block-0",
                            "action_type": "replace",
                            "new_html": '<p id="moss-block-0">updated</p>',
                        },
                        "id": "tool-1",
                    }
                ],
            ),
            ToolMessage(content="ok", tool_call_id="tool-1"),
        ]
        advanced_state = {**state, **first_context, "messages": messages}
        advanced_state.update(mark_global_batch_processed(advanced_state, ["moss-block-0"]))
        second_context = build_context_update(advanced_state)
        visible = _visible_conversation_messages({**advanced_state, **second_context, "messages": messages})

        self.assertEqual(second_context["current_batch_ids"], ["moss-block-1"])
        self.assertEqual(len(visible), 1)
        self.assertIsInstance(visible[0], HumanMessage)


if __name__ == "__main__":
    unittest.main()
