from __future__ import annotations

import operator
from typing import Annotated, TypedDict

from langchain_core.messages import BaseMessage


class AgentState(TypedDict):
    """单次 Agent 运行期间在 LangGraph 节点之间传递的状态对象。

    每次 `/chat-stream` 请求都会创建一个新的 AgentState。LangGraph 会把
    模型回复和工具执行结果追加到 `messages`，其余字段用于描述本轮请求
    中由前端提交的文档上下文和定位信息。
    """

    # 本轮图执行的消息轨迹。operator.add 作为 reducer，允许 LangGraph
    # 把每个节点返回的 AI 消息或工具消息追加到现有 messages 列表中。
    messages: Annotated[list[BaseMessage], operator.add]

    # 前端编辑器发送的最新 HTML 快照。Agent 在本轮问答和文档修改规划中，
    # 只能以它作为当前文档事实来源。
    canvas_snapshot: str

    # 当前光标或用户指令对应的精确锚点 ID，可能指向段落、列表项等嵌套节点。
    # 该 ID 应当真实存在于 canvas_snapshot 中。
    focus_element_id: str | None

    # 包含 focus_element_id 的稳定顶层文档块 ID。用于上下文检索和编辑时的
    # 块级兜底定位，避免只依赖嵌套锚点导致修改范围不清。
    focus_block_id: str | None

    # 前端会话 ID。当前会作为 LangGraph thread_id 和日志字段使用；
    # 是否真正具备跨请求记忆，取决于后续是否接入 checkpointer。
    session_id: str

    # 单次请求 ID，用于关联本轮运行中的 LLM 输入输出日志。
    request_id: str
