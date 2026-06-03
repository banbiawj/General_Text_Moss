from __future__ import annotations

import operator
from typing import Annotated, Any, Literal, TypedDict

from langchain_core.messages import BaseMessage


TaskType = Literal["general_chat", "document_qa", "local_edit", "global_edit"]
TaskStatus = Literal["pending", "running", "done", "failed"]


class AgentTask(TypedDict, total=False):
    """Agent 本轮要处理的一个任务。

    不管用户请求是普通聊天、文档问答、局部修改还是全文整理，最终都拆成
    一个或多个局部任务。每个任务只暴露本任务需要的大模型上下文，不直接
    暴露完整 canvas_snapshot。
    """

    # 任务唯一 ID，用于日志、调试、前端进度显示。
    task_id: str

    # 只用于任务的处理的工作记忆
    task_message:Annotated[list[BaseMessage], operator.add]

    # 本任务允许进入大模型 prompt 的裁剪上下文。
    # 注意：不是完整 canvas_snapshot。
    canvas_context: str
    canvas_context_blocks: list[dict[str, Any]]
    block_ref_map: dict[str, str]
    canvas_context_operation_seq: int

    # 基于任务意图生成的任务提示词。
    # 例如：局部润色、文档问答、全文第 N 段整理等。
    task_prompt: str

    # 本任务允许调用的工具名。
    # 例如：["search_document_blocks", "update_canvas_element"]。
    task_tools: list[str]

    # 本任务允许修改或引用为修改目标的元素 ID。
    # update_canvas_element 必须校验 element_id 在这里。
    allowed_element_ids: list[str]

    # 本任务的工具预算使用量。键是预算组名，值是已经消耗的次数。
    tool_budget_usage: dict[str, int]

    # 任务状态，用于推进任务列表。
    status: TaskStatus

    # 可选：任务失败原因。
    error: str


class AgentTaskResult(TypedDict, total=False):
    """Result emitted by one independently executed task branch."""

    task_id: str
    task_index: int
    request_id: str
    status: TaskStatus
    messages: list[BaseMessage]
    pending_mutations: list[dict]
    error: str


class AgentState(TypedDict, total=False):
    """单次 Agent 运行期间在 LangGraph 节点之间传递的简化状态。

    状态只保存请求事实、工作记忆和任务队列。完整文档快照保留在
    canvas_snapshot 中供后端内部解析和裁剪；真正进入大模型 prompt 的内容
    应来自当前 AgentTask.canvas_context 与 AgentTask.task_prompt。
    """

    # 本轮图执行的消息轨迹。operator.add 作为 reducer，允许 LangGraph
    # 把每个节点返回的 AI 消息或工具消息追加到现有 messages 列表中。
    messages: Annotated[list[BaseMessage], operator.add]

    # 用户本轮输入的原始自然语言指令。
    user_input: str

    # 前端编辑器发送的完整 HTML 快照。它是后端内部的事实来源，用于任务规划、
    # 上下文裁剪、检索和修改校验；不能原样塞进大模型 prompt。
    canvas_snapshot: str

    # 当前光标精确位置 ID，可能是嵌套节点。
    focus_element_id: str | None

    # 当前光标所属顶层块 ID。
    focus_block_id: str | None

    # 任务类型。全文任务会被拆成多个局部 task，但仍可标记为 global_edit。
    task_type: TaskType
    task_reason:str #判断原因，一句话

    # 本轮拆解出的任务列表。
    # 普通聊天：1 个 task
    # 文档问答：1 个或多个检索/问答 task
    # 局部修改：通常 1 个 task
    # 全文整理：多个局部 task
    tasks: list[AgentTask]

    # 当前正在处理的任务下标。
    current_task_index: int

    # 待发送到前端的 DOM 变更指令队列。
    # tools_node 在调用 update_canvas_element 时追加到此列表，
    # stream_agent_events 将其作为 dom_mutation SSE 事件发送后清空。
    pending_mutations: list[dict]

    # Send 分支返回的任务结果。operator.add 允许多个 task_worker 并发追加结果。
    task_results: Annotated[list[AgentTaskResult], operator.add]

    # 会话与日志字段，保留给后端运行时使用。
    session_id: str
    conversation_id: str

    request_id: str
