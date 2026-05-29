from __future__ import annotations

import unittest
import json

from langchain_core.messages import AIMessage

from app.agent.graph import task_assemble_node, tools_node


def _snapshot(block_count: int) -> str:
    return "".join(
        f'<p id="moss-block-{index}">block {index}</p>'
        for index in range(block_count)
    )


class CanvasPagingGraphTests(unittest.TestCase):
    def test_task_assemble_seeds_structured_context_blocks(self) -> None:
        state = {
            "task_type": "document_qa",
            "canvas_snapshot": _snapshot(6),
            "focus_block_id": "moss-block-2",
            "focus_element_id": "moss-block-2",
            "user_input": "What comes next?",
        }

        result = task_assemble_node(state)
        task = result["tasks"][0]

        self.assertIn("canvas_context_blocks", task)
        self.assertEqual([block["block_id"] for block in task["canvas_context_blocks"]], [
            "moss-block-0",
            "moss-block-1",
            "moss-block-2",
            "moss-block-3",
            "moss-block-4",
            "moss-block-5",
        ])

    def test_tools_node_injects_state_and_merges_read_after_result_into_task_context(self) -> None:
        initial_state = {
            "messages": [],
            "user_input": "What comes next?",
            "canvas_snapshot": _snapshot(6),
            "focus_element_id": "moss-block-1",
            "focus_block_id": "moss-block-1",
            "task_type": "document_qa",
            "task_reason": "",
            "current_task_index": 0,
            "pending_mutations": [],
        }
        assembled = task_assemble_node(initial_state)
        task = assembled["tasks"][0]
        task["canvas_context_blocks"] = [
            block for block in task["canvas_context_blocks"]
            if block["block_id"] in {"moss-block-1", "moss-block-2"}
        ]
        task["canvas_context"] = '<p id="moss-block-1">block 1</p><p id="moss-block-2">block 2</p>'
        task["task_message"] = [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "canvas_read_after",
                        "args": {"block_count": 2},
                        "id": "call-read-after",
                        "type": "tool_call",
                    }
                ],
            )
        ]
        state = {**initial_state, "tasks": [task]}

        result = tools_node(state)
        updated_task = result["tasks"][0]

        self.assertEqual([block["block_id"] for block in updated_task["canvas_context_blocks"]], [
            "moss-block-1",
            "moss-block-2",
            "moss-block-3",
            "moss-block-4",
        ])
        self.assertIn('id="moss-block-4"', updated_task["canvas_context"])
        self.assertIn('id="moss-block-4"', updated_task["task_prompt"])

    def test_tools_node_merges_read_before_result_in_snapshot_order(self) -> None:
        initial_state = {
            "messages": [],
            "user_input": "What came before?",
            "canvas_snapshot": _snapshot(6),
            "focus_element_id": "moss-block-3",
            "focus_block_id": "moss-block-3",
            "task_type": "document_qa",
            "task_reason": "",
            "current_task_index": 0,
            "pending_mutations": [],
        }
        assembled = task_assemble_node(initial_state)
        task = assembled["tasks"][0]
        task["canvas_context_blocks"] = [
            block for block in task["canvas_context_blocks"]
            if block["block_id"] in {"moss-block-3", "moss-block-4"}
        ]
        task["canvas_context"] = '<p id="moss-block-3">block 3</p><p id="moss-block-4">block 4</p>'
        task["task_message"] = [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "canvas_read_before",
                        "args": {"block_count": 2},
                        "id": "call-read-before",
                        "type": "tool_call",
                    }
                ],
            )
        ]
        state = {**initial_state, "tasks": [task]}

        result = tools_node(state)
        updated_task = result["tasks"][0]
        rendered = updated_task["canvas_context"]

        self.assertLess(rendered.index("moss-block-1"), rendered.index("moss-block-4"))
        self.assertEqual([block["block_id"] for block in updated_task["canvas_context_blocks"]], [
            "moss-block-1",
            "moss-block-2",
            "moss-block-3",
            "moss-block-4",
        ])

    def test_tools_node_rejects_update_canvas_element_for_missing_element_id(self) -> None:
        task = {
            "task_message": [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "update_canvas_element",
                            "args": {
                                "element_id": "moss-block-53",
                                "action_type": "replace",
                                "new_html": '<p id="moss-block-53">wrong</p>',
                            },
                            "id": "call-update-missing",
                            "type": "tool_call",
                        }
                    ],
                )
            ],
            "canvas_context_blocks": [],
            "canvas_context_operation_seq": 0,
            "task_tools": ["update_canvas_element"],
            "task_prompt": "",
        }
        state = {
            "messages": [],
            "user_input": "Rewrite this",
            "canvas_snapshot": '<p id="moss-block-real">real</p>',
            "focus_element_id": "moss-block-real",
            "focus_block_id": "moss-block-real",
            "task_type": "global_edit",
            "task_reason": "",
            "current_task_index": 0,
            "pending_mutations": [],
            "tasks": [task],
        }

        result = tools_node(state)

        self.assertEqual(result["pending_mutations"], [])
        tool_message = result["tasks"][0]["task_message"][-1]
        payload = json.loads(tool_message.content)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"], "element_id_not_found")
        self.assertEqual(payload["element_id"], "moss-block-53")

    def test_document_qa_prompt_exposes_paging_tools(self) -> None:
        state = {
            "task_type": "document_qa",
            "canvas_snapshot": _snapshot(4),
            "focus_block_id": "moss-block-1",
            "focus_element_id": "moss-block-1",
            "user_input": "Read around this point",
        }

        task = task_assemble_node(state)["tasks"][0]

        self.assertIn("canvas_read_before", task["task_prompt"])
        self.assertIn("canvas_read_after", task["task_prompt"])
        self.assertIn("ordered by their position in canvas_snapshot", task["task_prompt"])


if __name__ == "__main__":
    unittest.main()
