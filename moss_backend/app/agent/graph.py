from __future__ import annotations

import json
import operator
from pathlib import Path
from typing import Annotated, Any, AsyncGenerator, Literal, TypedDict
from uuid import uuid4

import yaml
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.prompts import PromptTemplate
from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send
from pydantic import BaseModel, Field

from app.agent.state import AgentState, AgentTask, AgentTaskResult, TaskType
from app.core.config import get_settings
from app.services.canvas_context import (
    context_blocks_from_html,
    merge_canvas_context_blocks,
    render_canvas_context,
)
from app.services.document_content import tailor_context
from app.tools.document_tools import DOCUMENT_TOOLS


PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"


def _ensure_langchain_legacy_debug_attr() -> None:
    try:
        import langchain
    except ImportError:
        return

    defaults = {"debug": False, "verbose": False, "llm_cache": None}
    for attr, value in defaults.items():
        if not hasattr(langchain, attr):
            setattr(langchain, attr, value)


_ensure_langchain_legacy_debug_attr()


def _load_prompt_template(filepath: str | Path) -> PromptTemplate:
    """Load a PromptTemplate from a YAML prompt file (safe replacement for deprecated load_prompt)."""
    path = Path(filepath)
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Invalid prompt format in {filepath}: expected a mapping, got {type(data).__name__}")
    return PromptTemplate(
        template=data["template"],
        input_variables=data.get("input_variables", []),
    )

# 根据任务类型映射可用的工具列表
TASK_TYPE_TOOLS: dict[TaskType, list[str]] = {
    "general_chat": [],
    "document_qa": ["search_document_blocks", "canvas_read_before", "canvas_read_after"],
    "local_edit": [
        "search_document_blocks",
        "canvas_read_before",
        "canvas_read_after",
        "update_canvas_element",
    ],
    "global_edit": ["update_canvas_element"],
}


STATEFUL_DOCUMENT_TOOL_NAMES = {
    "search_document_blocks",
    "canvas_read_before",
    "canvas_read_after",
    "update_canvas_element",
}


class TaskWorkerState(TypedDict, total=False):
    """Branch-local state for one Send-dispatched task."""

    tasks: list[AgentTask]
    current_task_index: int
    source_task_index: int
    conversation_messages: list[Any]
    user_input: str
    canvas_snapshot: str
    focus_element_id: str | None
    focus_block_id: str | None
    task_type: TaskType
    task_reason: str
    worker_pending_mutations: Annotated[list[dict], operator.add]
    task_results: Annotated[list[AgentTaskResult], operator.add]
    session_id: str
    conversation_id: str
    request_id: str


class TaskWorkerOutputState(TypedDict, total=False):
    task_results: Annotated[list[AgentTaskResult], operator.add]


def _prompt_template_for_task_type(task_type: TaskType) -> PromptTemplate:
    if task_type == "document_qa":
        return _load_prompt_template(PROMPTS_DIR / "document_qa_prompt.yaml")
    if task_type == "local_edit":
        return _load_prompt_template(PROMPTS_DIR / "local_edit_prompt.yaml")
    if task_type == "global_edit":
        return _load_prompt_template(PROMPTS_DIR / "global_edit_prompt.yaml")
    return _load_prompt_template(PROMPTS_DIR / "general_chat_prompt.yaml")


def _format_task_prompt(
    *,
    task_type: TaskType,
    user_input: str,
    canvas_context: str,
    focus_element_id: str | None,
    focus_block_id: str | None,
    task_tools: list[str],
) -> str:
    return _prompt_template_for_task_type(task_type).format(
        user_input=user_input,
        canvas_context=canvas_context,
        focus_element_id=focus_element_id or "",
        focus_block_id=focus_block_id or "",
        task_tools=str(task_tools),
    )


# ── Intent Node ──────────────────────────────────────────────────────────


IntentCandidateType = Literal[
    "general_chat",
    "document_qa",
    "local_edit",
    "global_edit",
    "ambiguous",
]


class IntentCandidateOutput(BaseModel):
    """Structured output from the intent classifier LLM."""

    task_type: IntentCandidateType = Field(description="意图分类结果")
    task_reason: str = Field(description="判断原因，一句话说明为什么归为该类别")


class IntentOutput(BaseModel):
    """Executable intent output used by the graph after ambiguity is resolved."""

    task_type: TaskType = Field(description="意图分类结果")
    task_reason: str = Field(description="判断原因，一句话说明为什么归为该类别")


def _invoke_intent_classifier(
    *,
    output_schema: type[BaseModel],
    system_prompt: str,
    user_content: str,
) -> BaseModel:
    settings = get_settings()
    llm = (
        ChatOpenAI(
            api_key=settings.llm_api_key,
            base_url=settings.llm_base_url,
            model=settings.llm_model,
            temperature=settings.llm_temperature,
        )
        .with_structured_output(output_schema, method="function_calling")
    )
    return llm.invoke(
        [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_content),
        ]
    )


def _message_role_for_intent(message: Any) -> str | None:
    if isinstance(message, HumanMessage):
        return "user"
    if isinstance(message, AIMessage):
        return "assistant"
    return None


def _truncate_intent_text(text: str, limit: int = 500) -> str:
    normalized = " ".join(str(text or "").split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 1].rstrip() + "…"


def _format_recent_intent_history(
    messages: list[Any],
    *,
    current_user_input: str,
    limit: int = 8,
) -> str:
    intent_messages = [
        message
        for message in messages
        if _message_role_for_intent(message) is not None
    ]

    if intent_messages and isinstance(intent_messages[-1], HumanMessage):
        last_content = _message_content_text(getattr(intent_messages[-1], "content", ""))
        if last_content == current_user_input:
            intent_messages = intent_messages[:-1]

    recent = intent_messages[-limit:]
    if not recent:
        return "(无)"

    lines: list[str] = []
    for message in recent:
        role = _message_role_for_intent(message)
        content = _truncate_intent_text(
            _message_content_text(getattr(message, "content", ""))
        )
        if role and content:
            lines.append(f"{role}: {content}")
    return "\n".join(lines) if lines else "(无)"


def _format_contextual_intent_payload(state: AgentState) -> str:
    user_input = state.get("user_input", "")
    canvas_snapshot = state.get("canvas_snapshot", "")
    focus_block_id = state.get("focus_block_id") or ""
    focus_element_id = state.get("focus_element_id") or ""
    has_canvas_snapshot = bool(str(canvas_snapshot or "").strip())
    has_focus_block = bool(focus_block_id)
    recent_history = _format_recent_intent_history(
        list(state.get("messages", [])),
        current_user_input=user_input,
        limit=8,
    )

    return "\n".join(
        [
            "当前用户输入：",
            user_input,
            "",
            "当前文档状态：",
            f"- has_canvas_snapshot: {str(has_canvas_snapshot).lower()}",
            f"- has_focus_block: {str(has_focus_block).lower()}",
            f"- focus_block_id: {focus_block_id}",
            f"- focus_element_id: {focus_element_id}",
            "",
            "最近 8 条聊天记录：",
            recent_history,
        ]
    )


def intent_node(state: AgentState) -> dict[str, Any]:
    """Use LLM to classify user intent, then create a task in state.tasks."""
    settings = get_settings()

    if settings.enable_mock_llm:
        return {
            "task_type": "general_chat",
            "task_reason": "mock（ENABLE_MOCK_LLM=true，跳过意图识别）",
        }

    system_prompt = _load_prompt_template(PROMPTS_DIR / "intent_prompt.yaml").format()
    result = _invoke_intent_classifier(
        output_schema=IntentCandidateOutput,
        system_prompt=system_prompt,
        user_content=state.get("user_input", ""),
    )

    if result.task_type == "ambiguous":
        contextual_prompt = _load_prompt_template(
            PROMPTS_DIR / "contextual_intent_prompt.yaml"
        ).format()
        contextual_result = _invoke_intent_classifier(
            output_schema=IntentOutput,
            system_prompt=contextual_prompt,
            user_content=_format_contextual_intent_payload(state),
        )
        return {
            "task_type": contextual_result.task_type,
            "task_reason": contextual_result.task_reason,
        }

    return {
        "task_type": result.task_type,
        "task_reason": result.task_reason,
    }


# ── Task Assemble Node ────────────────────────────────────────────────────


def task_assemble_node(state: AgentState) -> dict[str, Any]:
    """根据意图分类结果组装任务列表：裁剪上下文、获取工具列表、生成提示词。"""
    task_type: TaskType = state.get("task_type", "general_chat")
    canvas_snapshot = state.get("canvas_snapshot", "")
    focus_block_id = state.get("focus_block_id")
    focus_element_id = state.get("focus_element_id", "")
    user_input = state.get("user_input", "")

    # 1. 根据 task_type 获取允许的工具列表
    task_tools = TASK_TYPE_TOOLS.get(task_type, [])

    # 2. 根据 task_type 裁剪 canvas 上下文
    if task_type == "general_chat":
        context_chunks = [""]
        prompt = _load_prompt_template(
        PROMPTS_DIR / "general_chat_prompt.yaml"
        )
    elif task_type == "document_qa":
        # 文档问答需要完整的画布内容用于检索
        tailored = tailor_context(canvas_snapshot, focus_block_id, task_type)
        context_chunks = tailored if tailored else [""]
        prompt = _load_prompt_template(
        PROMPTS_DIR / "document_qa_prompt.yaml"
        )
    elif task_type == "local_edit":
        tailored = tailor_context(canvas_snapshot, focus_block_id, task_type)
        context_chunks = tailored if tailored else [""]
        prompt = _load_prompt_template(
        PROMPTS_DIR / "local_edit_prompt.yaml"
        )
    elif task_type == "global_edit":
        tailored = tailor_context(canvas_snapshot, focus_block_id, task_type)
        context_chunks = tailored if tailored else [""]
        prompt = _load_prompt_template(
        PROMPTS_DIR / "global_edit_prompt.yaml"
        )
    else:
        context_chunks = [""]
        prompt = _load_prompt_template(
        PROMPTS_DIR / "general_chat_prompt.yaml"
        )

    # 3. 加载提示词模板并组装 task_prompt


    tasks: list[AgentTask] = []
    for chunk in context_chunks:
        canvas_context_blocks = context_blocks_from_html(
            canvas_snapshot=canvas_snapshot,
            context_html=chunk,
            source="initial",
            added_at=0,
        )
        rendered_context = render_canvas_context(canvas_context_blocks) if canvas_context_blocks else chunk
        task_prompt = _format_task_prompt(
            task_type=task_type,
            user_input=user_input,
            canvas_context=rendered_context,
            focus_element_id=focus_element_id,
            focus_block_id=focus_block_id,
            task_tools=task_tools,
        )
        task = AgentTask(
            task_id=uuid4().hex,
            task_message=[],
            canvas_context=rendered_context,
            canvas_context_blocks=canvas_context_blocks,
            canvas_context_operation_seq=0,
            task_prompt=task_prompt,
            task_tools=task_tools,
            allowed_element_ids=[],
            status="pending",
        )
        tasks.append(task)

    return {"tasks": tasks}


# ── Execute Node (ReAct) ─────────────────────────────────────────────────


MAX_CONVERSATION_HISTORY_MESSAGES = 8


def _build_execute_messages(
    *,
    system_prompt: str,
    conversation_messages: list[Any],
    task_messages: list[Any],
) -> list[Any]:
    bounded_history = conversation_messages[-MAX_CONVERSATION_HISTORY_MESSAGES:]
    return [SystemMessage(content=system_prompt)] + bounded_history + task_messages


def execute_node(state: AgentState) -> dict[str, Any]:
    """Execute the current task via LLM with optional tool calling (ReAct).

    Uses the task's prompt as system instruction and task_message as working
    memory.  If the LLM emits tool calls they are routed to the tools node;
    otherwise the response is treated as the final answer and the task is
    marked done.
    """
    settings = get_settings()
    current_idx = state["current_task_index"]
    tasks = list(state["tasks"])
    task = tasks[current_idx]

    if settings.enable_mock_llm:
        response = AIMessage(
            content=f"（Mock 回复）收到您的消息，当前任务类型已识别。",
        )
        task_messages = list(task.get("task_message", []))
        updated_messages = task_messages + [response]
        tasks[current_idx] = {**task, "task_message": updated_messages, "status": "done"}
        return {"tasks": tasks, "messages": [response]}

    # Only expose tools allowed for this task
    tools = [t for t in DOCUMENT_TOOLS if t.name in task.get("task_tools", [])]

    llm = ChatOpenAI(
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
        model=settings.llm_model,
        temperature=settings.llm_temperature,
    )
    if tools:
        llm = llm.bind_tools(tools)

    task_messages = list(task.get("task_message", []))
    conversation_messages = list(state.get("messages", []))
    messages = _build_execute_messages(
        system_prompt=task["task_prompt"],
        conversation_messages=conversation_messages,
        task_messages=task_messages,
    )

    response = llm.invoke(messages)

    # Append the LLM response to task working memory
    updated_messages = task_messages + [response]
    tasks[current_idx] = {**task, "task_message": updated_messages}

    if getattr(response, "tool_calls", None):
        tasks[current_idx] = {**tasks[current_idx], "status": "running"}
        return {"tasks": tasks}
    else:
        # Final answer – mark done and publish to global messages
        tasks[current_idx] = {**tasks[current_idx], "status": "done"}
        return {"tasks": tasks, "messages": [response]}


def _worker_task_result(
    *,
    state: TaskWorkerState,
    task: AgentTask,
    messages: list[Any],
) -> AgentTaskResult:
    return AgentTaskResult(
        task_id=str(task.get("task_id", "")),
        task_index=int(state.get("source_task_index", state.get("current_task_index", 0))),
        request_id=str(state.get("request_id", "")),
        status=task.get("status", "done"),
        messages=messages,
        pending_mutations=list(state.get("worker_pending_mutations", [])),
    )


def execute_task_node(state: TaskWorkerState) -> dict[str, Any]:
    """Execute one Send-dispatched task while keeping task-local state isolated."""
    settings = get_settings()
    current_idx = state.get("current_task_index", 0)
    tasks = list(state.get("tasks", []))
    task = tasks[current_idx]

    if settings.enable_mock_llm:
        response = AIMessage(
            content=f"（Mock 回复）收到您的消息，当前任务类型已识别。",
        )
        task_messages = list(task.get("task_message", []))
        updated_messages = task_messages + [response]
        updated_task = AgentTask(
            **{**task, "task_message": updated_messages, "status": "done"}
        )
        tasks[current_idx] = updated_task
        return {
            "tasks": tasks,
            "task_results": [
                _worker_task_result(
                    state=state,
                    task=updated_task,
                    messages=[response],
                )
            ],
        }

    tools = [t for t in DOCUMENT_TOOLS if t.name in task.get("task_tools", [])]

    llm = ChatOpenAI(
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
        model=settings.llm_model,
        temperature=settings.llm_temperature,
    )
    if tools:
        llm = llm.bind_tools(tools)

    task_messages = list(task.get("task_message", []))
    conversation_messages = list(state.get("conversation_messages", []))
    messages = _build_execute_messages(
        system_prompt=task["task_prompt"],
        conversation_messages=conversation_messages,
        task_messages=task_messages,
    )

    response = llm.invoke(messages)
    updated_messages = task_messages + [response]
    tasks[current_idx] = {**task, "task_message": updated_messages}

    if getattr(response, "tool_calls", None):
        tasks[current_idx] = {**tasks[current_idx], "status": "running"}
        return {"tasks": tasks}

    tasks[current_idx] = {**tasks[current_idx], "status": "done"}
    return {
        "tasks": tasks,
        "task_results": [
            _worker_task_result(
                state=state,
                task=tasks[current_idx],
                messages=[response],
            )
        ],
    }


# ── Custom Tools Node ────────────────────────────────────────────────────


def _apply_canvas_context_tool_result(
    *,
    state: dict[str, Any],
    task: AgentTask,
    result_str: str,
) -> AgentTask:
    try:
        payload = json.loads(result_str)
    except json.JSONDecodeError:
        return task

    if not isinstance(payload, dict) or payload.get("operation") != "canvas_context_add":
        return task

    new_blocks = payload.get("blocks")
    if not isinstance(new_blocks, list) or not new_blocks:
        return task

    existing_blocks = task.get("canvas_context_blocks", [])
    if not isinstance(existing_blocks, list):
        existing_blocks = []

    merged_blocks = merge_canvas_context_blocks(existing_blocks, new_blocks)
    rendered_context = render_canvas_context(merged_blocks)
    try:
        operation_seq = int(task.get("canvas_context_operation_seq", 0)) + 1
    except (TypeError, ValueError):
        operation_seq = 1
    task_tools = list(task.get("task_tools", []))
    task_type: TaskType = state.get("task_type", "general_chat")

    return AgentTask(
        **{
            **task,
            "canvas_context_blocks": merged_blocks,
            "canvas_context_operation_seq": operation_seq,
            "canvas_context": rendered_context,
            "task_prompt": _format_task_prompt(
                task_type=task_type,
                user_input=state.get("user_input", ""),
                canvas_context=rendered_context,
                focus_element_id=state.get("focus_element_id"),
                focus_block_id=state.get("focus_block_id"),
                task_tools=task_tools,
            ),
        }
    )


def _run_tools_for_current_task(state: dict[str, Any]) -> tuple[list[AgentTask], list[dict]]:
    current_idx = state["current_task_index"]
    tasks = list(state["tasks"])
    task = tasks[current_idx]
    task_messages = list(task.get("task_message", []))
    last_msg = task_messages[-1]

    tool_results: list[ToolMessage] = []
    pending_mutations: list[dict] = []

    for tool_call in last_msg.tool_calls:
        tool = next(
            (t for t in DOCUMENT_TOOLS if t.name == tool_call["name"]),
            None,
        )
        if not tool:
            tool_results.append(
                ToolMessage(
                    content=f"Tool '{tool_call['name']}' not found.",
                    tool_call_id=tool_call["id"],
                )
            )
            continue

        try:
            args = dict(tool_call["args"])
            if tool_call["name"] in STATEFUL_DOCUMENT_TOOL_NAMES:
                args["state"] = state
            result = tool.invoke(args)
            result_str = str(result) if result is not None else ""
            task = _apply_canvas_context_tool_result(
                state=state,
                task=task,
                result_str=result_str,
            )
        except Exception as e:
            result_str = f"Tool error: {e}"

        tool_results.append(
            ToolMessage(content=result_str, tool_call_id=tool_call["id"])
        )

        # Capture DOM mutations only after update_canvas_element validates the target.
        if tool_call["name"] == "update_canvas_element":
            try:
                mutation_payload = json.loads(result_str)
            except json.JSONDecodeError:
                mutation_payload = {}
            if isinstance(mutation_payload, dict) and mutation_payload.get("ok") is True:
                pending_mutations.append({
                    "element_id": tool_call["args"].get("element_id", ""),
                    "action_type": tool_call["args"].get("action_type", ""),
                    "new_html": tool_call["args"].get("new_html", ""),
                })

    tasks[current_idx] = {**task, "task_message": task_messages + tool_results}
    return tasks, pending_mutations


def tools_node(state: AgentState) -> dict[str, Any]:
    """Execute tool calls for the current task and append results to task_message.

    When ``update_canvas_element`` is invoked, captures the mutation args into
    ``pending_mutations`` so that ``stream_agent_events`` can relay them to the
    frontend as ``dom_mutation`` SSE events.
    """
    tasks, pending_mutations = _run_tools_for_current_task(state)
    return {"tasks": tasks, "pending_mutations": pending_mutations}


def worker_tools_node(state: TaskWorkerState) -> dict[str, Any]:
    """Execute tool calls inside one Send branch without writing parent state."""
    tasks, pending_mutations = _run_tools_for_current_task(state)
    return {"tasks": tasks, "worker_pending_mutations": pending_mutations}


# ── Task Advance Node ────────────────────────────────────────────────────


def task_advance_node(state: AgentState) -> dict[str, Any]:
    """Advance to the next task index."""
    return {"current_task_index": state["current_task_index"] + 1}


# ── Routers ──────────────────────────────────────────────────────────────


def router_execute(state: AgentState) -> str:
    """From execute: route to tools if the last message has tool_calls, else advance."""
    current_idx = state["current_task_index"]
    task = state["tasks"][current_idx]
    msgs = task.get("task_message", [])
    if msgs and getattr(msgs[-1], "tool_calls", None):
        return "tools"
    return "task_advance"


def router_task_advance(state: AgentState) -> str:
    """From task_advance: route to execute if more tasks remain, otherwise END."""
    current_idx = state["current_task_index"]
    if current_idx < len(state["tasks"]):
        return "execute"
    return END


def router_execute_task(state: TaskWorkerState) -> str:
    """From a worker execute node: route to tools while the active task requests tools."""
    current_idx = state.get("current_task_index", 0)
    tasks = state.get("tasks", [])
    if not tasks:
        return END
    task = tasks[current_idx]
    msgs = task.get("task_message", [])
    if msgs and getattr(msgs[-1], "tool_calls", None):
        return "tools"
    return END


def route_tasks(state: AgentState) -> list[Send] | str:
    """Fan out assembled tasks to isolated worker subgraphs."""
    tasks = list(state.get("tasks", []))
    if not tasks:
        return "reduce"

    return [
        Send(
            "task_worker",
            {
                "tasks": [task],
                "current_task_index": 0,
                "source_task_index": index,
                "conversation_messages": list(state.get("messages", [])),
                "user_input": state.get("user_input", ""),
                "canvas_snapshot": state.get("canvas_snapshot", ""),
                "focus_element_id": state.get("focus_element_id"),
                "focus_block_id": state.get("focus_block_id"),
                "task_type": state.get("task_type", "general_chat"),
                "task_reason": state.get("task_reason", ""),
                "worker_pending_mutations": [],
                "task_results": [],
                "session_id": state.get("session_id", ""),
                "conversation_id": state.get("conversation_id", ""),
                "request_id": state.get("request_id", ""),
            },
        )
        for index, task in enumerate(tasks)
    ]


def reduce_node(state: AgentState) -> dict[str, Any]:
    """Collect task worker outputs in document/task order for frontend streaming."""
    request_id = str(state.get("request_id", ""))
    all_results = list(state.get("task_results", []))
    if request_id:
        all_results = [
            result
            for result in all_results
            if str(result.get("request_id", "")) == request_id
        ]
    results = sorted(
        all_results,
        key=lambda result: int(result.get("task_index", 0)),
    )

    messages: list[Any] = []
    pending_mutations: list[dict] = []
    for result in results:
        messages.extend(list(result.get("messages", [])))
        pending_mutations.extend(list(result.get("pending_mutations", [])))

    return {"messages": messages, "pending_mutations": pending_mutations}


# ── Graph Definition ─────────────────────────────────────────────────────

worker_builder = StateGraph(TaskWorkerState, output=TaskWorkerOutputState)
worker_builder.add_node("execute", execute_task_node)
worker_builder.add_node("tools", worker_tools_node)
worker_builder.add_edge(START, "execute")
worker_builder.add_edge("tools", "execute")
worker_builder.add_conditional_edges(
    "execute",
    router_execute_task,
    {"tools": "tools", END: END},
)
task_worker_graph = worker_builder.compile()


builder = StateGraph(AgentState)
builder.add_node("intent", intent_node)
builder.add_node("task_assemble", task_assemble_node)
builder.add_node("task_worker", task_worker_graph)
builder.add_node("reduce", reduce_node)

builder.add_edge(START, "intent")
builder.add_edge("intent", "task_assemble")
builder.add_conditional_edges("task_assemble", route_tasks, ["task_worker", "reduce"])
builder.add_edge("task_worker", "reduce")
builder.add_edge("reduce", END)

# Legacy serial nodes remain importable for direct unit tests, but they are no
# longer attached to the main graph.

def compile_agent_graph(checkpointer: Any | None = None) -> Any:
    return builder.compile(checkpointer=checkpointer)


graph = compile_agent_graph()


# ── Streaming Entrypoint ─────────────────────────────────────────────────


def _sanitize_output(output: Any) -> Any:
    """Remove non-JSON-serializable objects (e.g. BaseMessage) from node output before SSE."""
    if isinstance(output, dict):
        return {k: _sanitize_output(v) for k, v in output.items() if not k.startswith("_")}
    if isinstance(output, list):
        return [_sanitize_output(item) for item in output]
    # Exclude BaseMessage and other non-serializable types
    if hasattr(output, "content") and hasattr(output, "type"):
        return {"content": str(getattr(output, "content", "")), "type": getattr(output, "type", "unknown")}
    try:
        import json
        json.dumps(output)
        return output
    except (TypeError, ValueError):
        return str(output)


def _frontend_message_role(message: Any) -> str | None:
    if isinstance(message, HumanMessage):
        return "user"
    if isinstance(message, AIMessage):
        return "ai"
    return None


def _message_content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        return "\n".join(part for part in parts if part)
    return str(content or "")


async def get_conversation_messages(
    compiled_graph: Any,
    conversation_id: str,
) -> list[dict[str, str]]:
    """Return checkpointed human/AI messages for frontend chat rendering."""
    state = await compiled_graph.aget_state(
        {"configurable": {"thread_id": conversation_id}},
    )
    values = getattr(state, "values", {}) or {}
    messages = values.get("messages", [])
    history: list[dict[str, str]] = []
    for message in messages:
        role = _frontend_message_role(message)
        content = _message_content_text(getattr(message, "content", ""))
        if role and content:
            history.append({"role": role, "content": content})
    return history


async def stream_agent_events(
    session_id: str,
    conversation_id: str,
    user_input: str,
    focus_element_id: str | None,
    focus_block_id: str | None,
    canvas_snapshot: str,
    compiled_graph: Any | None = None,
) -> AsyncGenerator[dict[str, Any], None]:
    """Run the agent graph and yield SSE-compatible events.

    Each yielded dict has the shape ``{"event": str, "data": dict}``,
    which the caller (routes.py) serialises into an SSE frame.
    """
    initial_state: dict[str, Any] = {
        "messages": [HumanMessage(content=user_input)],
        "user_input": user_input,
        "canvas_snapshot": canvas_snapshot,
        "focus_element_id": focus_element_id,
        "focus_block_id": focus_block_id,
        "task_type": "general_chat",
        "task_reason": "",
        "tasks": [],
        "current_task_index": 0,
        "pending_mutations": [],
        "session_id": session_id,
        "conversation_id": conversation_id,
        "request_id": uuid4().hex,
    }

    runtime_graph = compiled_graph or graph
    config = {"configurable": {"thread_id": conversation_id}}

    async for event in runtime_graph.astream_events(
        initial_state,
        config=config,
        version="v2",
    ):
        kind = event["event"]
        name = event.get("name", "")

        if kind == "on_chain_start" and name == "intent":
            yield {"event": "node_start", "data": {"node": "intent"}}
        elif kind == "on_chain_end" and name == "intent":
            output = _sanitize_output(event.get("data", {}).get("output", {}))
            yield {
                "event": "node_end",
                "data": {"node": "intent", "output": output},
            }
        elif kind == "on_chain_start" and name == "task_assemble":
            yield {"event": "node_start", "data": {"node": "task_assemble"}}
        elif kind == "on_chain_end" and name == "task_assemble":
            output = _sanitize_output(event.get("data", {}).get("output", {}))
            yield {
                "event": "node_end",
                "data": {"node": "task_assemble", "output": output},
            }
        elif kind == "on_chain_start" and name == "reduce":
            yield {"event": "node_start", "data": {"node": "reduce"}}
        elif kind == "on_chain_end" and name == "reduce":
            raw = event.get("data", {}).get("output", {})
            output = _sanitize_output(raw)
            yield {
                "event": "node_end",
                "data": {"node": "reduce", "output": output},
            }
            for msg in raw.get("messages", []):
                content = getattr(msg, "content", "") or ""
                if content:
                    yield {"event": "chat_chunk", "data": {"content": content, "done": True}}
            for mutation in raw.get("pending_mutations", []):
                yield {"event": "dom_mutation", "data": mutation}
        elif kind == "on_chain_start" and name == "execute":
            yield {"event": "node_start", "data": {"node": "execute"}}
        elif kind == "on_chain_end" and name == "execute":
            raw = event.get("data", {}).get("output", {})
            output = _sanitize_output(raw)
            yield {
                "event": "node_end",
                "data": {"node": "execute", "output": output},
            }
            # Publish AI message to frontend as chat_chunk
            for msg in raw.get("messages", []):
                content = getattr(msg, "content", "") or ""
                if content:
                    yield {"event": "chat_chunk", "data": {"content": content, "done": True}}
        elif kind == "on_chain_start" and name == "tools":
            yield {"event": "node_start", "data": {"node": "tools"}}
        elif kind == "on_chain_end" and name == "tools":
            raw = event.get("data", {}).get("output", {})
            output = _sanitize_output(raw)
            yield {
                "event": "node_end",
                "data": {"node": "tools", "output": output},
            }
            # Emit dom_mutation events to the frontend for each pending mutation
            for mutation in raw.get("pending_mutations", []):
                yield {"event": "dom_mutation", "data": mutation}
        elif kind == "on_chain_start" and name == "task_advance":
            yield {"event": "node_start", "data": {"node": "task_advance"}}
        elif kind == "on_chain_end" and name == "task_advance":
            output = _sanitize_output(event.get("data", {}).get("output", {}))
            yield {
                "event": "node_end",
                "data": {"node": "task_advance", "output": output},
            }
