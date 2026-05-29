from __future__ import annotations

import unittest

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.types import Send

from app.agent import graph as graph_module


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
