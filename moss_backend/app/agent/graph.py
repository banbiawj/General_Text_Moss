from __future__ import annotations

import json
import operator
from pathlib import Path
from time import perf_counter
from typing import Annotated, Any, AsyncGenerator, Literal, TypedDict
from uuid import uuid4

import yaml
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.prompts import PromptTemplate
from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send
from pydantic import BaseModel, Field

from app.agent.agent_mosslog import (
    log_agent_error,
    log_llm_request,
    log_llm_response,
    log_node_exit,
    log_route_decision,
    log_tool_result,
    log_user_input,
)
from app.agent.state import AgentState, AgentTask, AgentTaskResult, TaskType
from app.core.config import get_settings
from app.services.canvas_context import (
    assign_block_refs,
    block_id_for_block_ref,
    block_ref_for_block_id,
    block_ref_map_from_context_blocks,
    context_blocks_from_html,
    is_block_ref,
    merge_canvas_context_blocks,
    render_canvas_context,
    strip_moss_block_id,
)
from app.services.document_content import _extract_moss_blocks, tailor_context
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


def _effective_task_index(state: dict[str, Any]) -> Any:
    source_task_index = state.get("source_task_index")
    if source_task_index is not None:
        return source_task_index
    return state.get("current_task_index")


def _trace_context(state: dict[str, Any], task: dict[str, Any] | None = None) -> dict[str, Any]:
    context = {
        "session_id": state.get("session_id"),
        "conversation_id": state.get("conversation_id"),
        "request_id": state.get("request_id"),
        "task_type": state.get("task_type"),
        "current_task_index": state.get("current_task_index"),
        "source_task_index": state.get("source_task_index"),
        "task_index": _effective_task_index(state),
    }
    if task is not None:
        context["task_id"] = task.get("task_id")
        context["task_status"] = task.get("status")
    return {key: value for key, value in context.items() if value is not None}


def _visible_trace_fields(context: dict[str, Any]) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    if "task_index" in context:
        fields["task_index"] = context["task_index"]
    return fields


def _trace_payload(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump()
    if isinstance(value, dict):
        return {str(key): _trace_payload(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_trace_payload(item) for item in value]
    if isinstance(value, (SystemMessage, HumanMessage, AIMessage, ToolMessage)):
        return _trace_message(value)
    return value


def _trace_message(message: Any) -> Any:
    if isinstance(message, SystemMessage):
        role = "system"
    elif isinstance(message, HumanMessage):
        role = "user"
    elif isinstance(message, AIMessage):
        role = "assistant"
    elif isinstance(message, ToolMessage):
        role = "tool"
    else:
        return _trace_payload(message)

    payload: dict[str, Any] = {
        "role": role,
        "type": getattr(message, "type", type(message).__name__),
        "content": getattr(message, "content", ""),
    }
    tool_calls = getattr(message, "tool_calls", None)
    if tool_calls:
        payload["tool_calls"] = _trace_payload(tool_calls)
    invalid_tool_calls = getattr(message, "invalid_tool_calls", None)
    if invalid_tool_calls:
        payload["invalid_tool_calls"] = _trace_payload(invalid_tool_calls)
    tool_call_id = getattr(message, "tool_call_id", None)
    if tool_call_id:
        payload["tool_call_id"] = tool_call_id
    response_metadata = getattr(message, "response_metadata", None)
    if response_metadata:
        payload["response_metadata"] = _trace_payload(response_metadata)
    usage_metadata = getattr(message, "usage_metadata", None)
    if usage_metadata:
        payload["usage_metadata"] = _trace_payload(usage_metadata)
    return payload


def _trace_messages(messages: list[Any]) -> list[Any]:
    return [_trace_message(message) for message in messages]


def _trace_task(task: dict[str, Any]) -> dict[str, Any]:
    return {
        "task_id": task.get("task_id"),
        "status": task.get("status"),
        "task_prompt": task.get("task_prompt"),
        "task_tools": task.get("task_tools", []),
        "task_message": _trace_messages(list(task.get("task_message", []))),
        "canvas_context": task.get("canvas_context"),
        "canvas_context_blocks": _trace_payload(task.get("canvas_context_blocks", [])),
        "allowed_element_ids": task.get("allowed_element_ids", []),
        "tool_budget_usage": task.get("tool_budget_usage", {}),
    }


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
        "update_canvas_elements",
    ],
    "global_edit": [
        "canvas_read_before",
        "canvas_read_after",
        "update_canvas_elements",
    ],
}


TASK_TYPE_TOOL_BUDGETS: dict[TaskType, dict[str, dict[str, Any]] | None] = {
    "general_chat": None,
    "document_qa": None,
    "local_edit": None,
    "global_edit": {
        "context_read": {
            "tools": ["canvas_read_before", "canvas_read_after"],
            "limit": 1,
            "message": "全局编辑任务的上下文翻阅次数已用完。请基于当前 chunk 继续完成修改，不要继续调用翻阅工具。",
        }
    },
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


def _visible_focus_ref(
    *,
    context_blocks: list[dict[str, Any]],
    focus_block_id: str | None,
    fallback: str | None,
) -> str:
    return block_ref_for_block_id(context_blocks, focus_block_id) or (fallback or "")


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
            timeout=120,
            max_retries=2,
        )
        .with_structured_output(output_schema, method="function_calling")
    )
    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_content),
    ]
    return llm.invoke(messages)


async def _ainvoke_intent_classifier(
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
            timeout=120,
            max_retries=2,
        )
        .with_structured_output(output_schema, method="function_calling")
    )
    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_content),
    ]
    return await llm.ainvoke(messages)


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
            "",
            "最近 8 条聊天记录：",
            recent_history,
        ]
    )


def intent_node(state: AgentState) -> dict[str, Any]:
    """Use LLM to classify user intent, then create a task in state.tasks."""
    settings = get_settings()
    trace_context = _trace_context(state)

    if settings.enable_mock_llm:
        output = {
            "task_type": "general_chat",
            "task_reason": "mock（ENABLE_MOCK_LLM=true，跳过意图识别）",
        }
        log_route_decision(
            output["task_type"],
            reason=output["task_reason"],
            raw_payload={
                "output": output,
                "user_input": state.get("user_input", ""),
                **trace_context,
            },
        )
        return output

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
        output = {
            "task_type": contextual_result.task_type,
            "task_reason": contextual_result.task_reason,
        }
        log_route_decision(
            output["task_type"],
            reason=output["task_reason"],
            raw_payload={
                "output": output,
                "user_input": state.get("user_input", ""),
                "ambiguous_first_pass": _trace_payload(result),
                **trace_context,
            },
        )
        return output

    output = {
        "task_type": result.task_type,
        "task_reason": result.task_reason,
    }
    log_route_decision(
        output["task_type"],
        reason=output["task_reason"],
        raw_payload={
            "output": output,
            "user_input": state.get("user_input", ""),
            **trace_context,
        },
    )
    return output


# ── Task Assemble Node ────────────────────────────────────────────────────


async def aintent_node(state: AgentState) -> dict[str, Any]:
    """Use async LLM calls when the graph is streamed with astream_events."""
    settings = get_settings()
    trace_context = _trace_context(state)

    if settings.enable_mock_llm:
        return intent_node(state)

    system_prompt = _load_prompt_template(PROMPTS_DIR / "intent_prompt.yaml").format()
    result = await _ainvoke_intent_classifier(
        output_schema=IntentCandidateOutput,
        system_prompt=system_prompt,
        user_content=state.get("user_input", ""),
    )

    if result.task_type == "ambiguous":
        contextual_prompt = _load_prompt_template(
            PROMPTS_DIR / "contextual_intent_prompt.yaml"
        ).format()
        contextual_result = await _ainvoke_intent_classifier(
            output_schema=IntentOutput,
            system_prompt=contextual_prompt,
            user_content=_format_contextual_intent_payload(state),
        )
        output = {
            "task_type": contextual_result.task_type,
            "task_reason": contextual_result.task_reason,
        }
        log_route_decision(
            output["task_type"],
            reason=output["task_reason"],
            raw_payload={
                "output": output,
                "user_input": state.get("user_input", ""),
                "ambiguous_first_pass": _trace_payload(result),
                **trace_context,
            },
        )
        return output

    output = {
        "task_type": result.task_type,
        "task_reason": result.task_reason,
    }
    log_route_decision(
        output["task_type"],
        reason=output["task_reason"],
        raw_payload={
            "output": output,
            "user_input": state.get("user_input", ""),
            **trace_context,
        },
    )
    return output


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
        canvas_context_blocks = assign_block_refs(canvas_context_blocks)
        block_ref_map = block_ref_map_from_context_blocks(canvas_context_blocks)
        rendered_context = render_canvas_context(canvas_context_blocks) if canvas_context_blocks else chunk
        visible_focus_ref = _visible_focus_ref(
            context_blocks=canvas_context_blocks,
            focus_block_id=focus_block_id,
            fallback=focus_element_id,
        )
        task_prompt = _format_task_prompt(
            task_type=task_type,
            user_input=user_input,
            canvas_context=rendered_context,
            focus_element_id=visible_focus_ref,
            focus_block_id=visible_focus_ref,
            task_tools=task_tools,
        )
        task = AgentTask(
            task_id=uuid4().hex,
            task_message=[],
            canvas_context=rendered_context,
            canvas_context_blocks=canvas_context_blocks,
            block_ref_map=block_ref_map,
            canvas_context_operation_seq=0,
            task_prompt=task_prompt,
            task_tools=task_tools,
            allowed_element_ids=[],
            tool_budget_usage={},
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


def _invoke_llm_with_trace(
    *,
    llm: Any,
    messages: list[Any],
    settings: Any,
    state: dict[str, Any],
    task: dict[str, Any],
    node: str,
    tools: list[Any],
) -> Any:
    context = _trace_context(state, task)
    visible_context = _visible_trace_fields(context)
    trace_messages = _trace_messages(messages)
    tool_names = [getattr(tool, "name", str(tool)) for tool in tools]
    log_llm_request(
        settings.llm_model,
        trace_messages,
        raw_payload={
            "node": node,
            "messages": trace_messages,
            "tools": tool_names,
            "task": _trace_task(task),
            **context,
        },
        **visible_context,
    )
    started_at = perf_counter()
    try:
        response = llm.invoke(messages)
    except Exception as exc:
        log_agent_error("llm invoke failed", exc, node=node, **context)
        raise

    response_payload = _trace_message(response)
    log_llm_response(
        settings.llm_model,
        response_payload.get("content", response_payload),
        tool_call=response_payload.get("tool_calls"),
        duration_ms=round((perf_counter() - started_at) * 1000),
        usage=getattr(response, "usage_metadata", None) or getattr(response, "response_metadata", None),
        raw_payload={
            "node": node,
            "full_response": response_payload,
            **context,
        },
        **visible_context,
    )
    return response


async def _ainvoke_llm_with_trace(
    *,
    llm: Any,
    messages: list[Any],
    settings: Any,
    state: dict[str, Any],
    task: dict[str, Any],
    node: str,
    tools: list[Any],
) -> Any:
    context = _trace_context(state, task)
    visible_context = _visible_trace_fields(context)
    trace_messages = _trace_messages(messages)
    tool_names = [getattr(tool, "name", str(tool)) for tool in tools]
    log_llm_request(
        settings.llm_model,
        trace_messages,
        raw_payload={
            "node": node,
            "messages": trace_messages,
            "tools": tool_names,
            "task": _trace_task(task),
            **context,
        },
        **visible_context,
    )
    started_at = perf_counter()
    try:
        response = await llm.ainvoke(messages)
    except Exception as exc:
        log_agent_error("llm invoke failed", exc, node=node, **context)
        raise

    response_payload = _trace_message(response)
    log_llm_response(
        settings.llm_model,
        response_payload.get("content", response_payload),
        tool_call=response_payload.get("tool_calls"),
        duration_ms=round((perf_counter() - started_at) * 1000),
        usage=getattr(response, "usage_metadata", None) or getattr(response, "response_metadata", None),
        raw_payload={
            "node": node,
            "full_response": response_payload,
            **context,
        },
        **visible_context,
    )
    return response


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
        timeout=120,
        max_retries=2,
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

    response = _invoke_llm_with_trace(
        llm=llm,
        messages=messages,
        settings=settings,
        state=state,
        task=task,
        node="execute",
        tools=tools,
    )

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
        return {"tasks": tasks}

    tools = [t for t in DOCUMENT_TOOLS if t.name in task.get("task_tools", [])]

    llm = ChatOpenAI(
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
        model=settings.llm_model,
        temperature=settings.llm_temperature,
        timeout=120,
        max_retries=2,
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

    response = _invoke_llm_with_trace(
        llm=llm,
        messages=messages,
        settings=settings,
        state=state,
        task=task,
        node="execute",
        tools=tools,
    )
    updated_messages = task_messages + [response]
    tasks[current_idx] = {**task, "task_message": updated_messages}

    if getattr(response, "tool_calls", None):
        tasks[current_idx] = {**tasks[current_idx], "status": "running"}
        return {"tasks": tasks}

    tasks[current_idx] = {**tasks[current_idx], "status": "done"}
    return {"tasks": tasks}


# ── Custom Tools Node ────────────────────────────────────────────────────


async def aexecute_task_node(state: TaskWorkerState) -> dict[str, Any]:
    """Execute one Send-dispatched task with async LLM calls."""
    settings = get_settings()
    current_idx = state.get("current_task_index", 0)
    tasks = list(state.get("tasks", []))
    task = tasks[current_idx]

    if settings.enable_mock_llm:
        return execute_task_node(state)

    tools = [t for t in DOCUMENT_TOOLS if t.name in task.get("task_tools", [])]

    llm = ChatOpenAI(
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
        model=settings.llm_model,
        temperature=settings.llm_temperature,
        timeout=120,
        max_retries=2,
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

    response = await _ainvoke_llm_with_trace(
        llm=llm,
        messages=messages,
        settings=settings,
        state=state,
        task=task,
        node="execute",
        tools=tools,
    )
    updated_messages = task_messages + [response]
    tasks[current_idx] = {**task, "task_message": updated_messages}

    if getattr(response, "tool_calls", None):
        tasks[current_idx] = {**tasks[current_idx], "status": "running"}
        return {"tasks": tasks}

    tasks[current_idx] = {**tasks[current_idx], "status": "done"}
    return {"tasks": tasks}


def worker_finalize_node(state: TaskWorkerState) -> TaskWorkerOutputState:
    """Expose only branch results to the parent graph, not worker-local tasks."""
    current_idx = state.get("current_task_index", 0)
    tasks = list(state.get("tasks", []))
    if not tasks:
        return {"task_results": []}

    try:
        task = tasks[int(current_idx)]
    except (IndexError, TypeError, ValueError):
        task = tasks[0]

    messages = [
        message
        for message in list(task.get("task_message", []))
        if isinstance(message, AIMessage) and not getattr(message, "tool_calls", None)
    ]
    return {
        "task_results": [
            _worker_task_result(
                state=state,
                task=task,
                messages=messages[-1:],
            )
        ]
    }


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

    merged_blocks = assign_block_refs(merge_canvas_context_blocks(existing_blocks, new_blocks))
    block_ref_map = block_ref_map_from_context_blocks(merged_blocks)
    rendered_context = render_canvas_context(merged_blocks)
    try:
        operation_seq = int(task.get("canvas_context_operation_seq", 0)) + 1
    except (TypeError, ValueError):
        operation_seq = 1
    task_tools = list(task.get("task_tools", []))
    task_type: TaskType = state.get("task_type", "general_chat")
    visible_focus_ref = _visible_focus_ref(
        context_blocks=merged_blocks,
        focus_block_id=state.get("focus_block_id"),
        fallback=state.get("focus_element_id"),
    )

    return AgentTask(
        **{
            **task,
            "canvas_context_blocks": merged_blocks,
            "block_ref_map": block_ref_map,
            "canvas_context_operation_seq": operation_seq,
            "canvas_context": rendered_context,
            "task_prompt": _format_task_prompt(
                task_type=task_type,
                user_input=state.get("user_input", ""),
                canvas_context=rendered_context,
                focus_element_id=visible_focus_ref,
                focus_block_id=visible_focus_ref,
                task_tools=task_tools,
            ),
        }
    )


def _tool_budget_config(
    *,
    task_type: TaskType,
    tool_name: str,
) -> tuple[str, dict[str, Any]] | None:
    budgets = TASK_TYPE_TOOL_BUDGETS.get(task_type)
    if not budgets:
        return None

    for group_name, group_config in budgets.items():
        if tool_name in set(group_config.get("tools", [])):
            return group_name, group_config
    return None


def _tool_budget_exceeded_result(
    *,
    group_name: str,
    tool_name: str,
    limit: int,
    used: int,
    message: str,
) -> str:
    return json.dumps(
        {
            "ok": False,
            "error": "tool_budget_exceeded",
            "budget_group": group_name,
            "tool": tool_name,
            "limit": limit,
            "used": used,
            "message": message,
        },
        ensure_ascii=False,
    )


def _consume_tool_budget(
    *,
    state: dict[str, Any],
    task: AgentTask,
    tool_name: str,
) -> tuple[AgentTask, str | None]:
    task_type: TaskType = state.get("task_type", "general_chat")
    budget_config = _tool_budget_config(task_type=task_type, tool_name=tool_name)
    if budget_config is None:
        return task, None

    group_name, group = budget_config
    usage = dict(task.get("tool_budget_usage", {}))
    used = int(usage.get(group_name, 0))
    limit = int(group.get("limit", 0))
    message = str(group.get("message", "Tool budget exceeded."))

    if used >= limit:
        return task, _tool_budget_exceeded_result(
            group_name=group_name,
            tool_name=tool_name,
            limit=limit,
            used=used,
            message=message,
        )

    usage[group_name] = used + 1
    return AgentTask(**{**task, "tool_budget_usage": usage}), None


def _unknown_block_ref_result(block_ref: Any, tool_name: str) -> str:
    return json.dumps(
        {
            "ok": False,
            "operation": tool_name,
            "error": "unknown_block_ref",
            "block_ref": block_ref,
            "message": "block_ref is not available in the current canvas context.",
            "hint": "Use one of the block references shown in the current canvas_context, such as b1 or b2.",
        },
        ensure_ascii=False,
    )


def _task_context_blocks(task: AgentTask) -> list[dict[str, Any]]:
    context_blocks = task.get("canvas_context_blocks", [])
    return context_blocks if isinstance(context_blocks, list) else []


def _resolve_block_ref_from_task(task: AgentTask, block_ref: Any) -> str | None:
    context_blocks = _task_context_blocks(task)
    element_id = block_id_for_block_ref(context_blocks, block_ref)
    if element_id:
        return element_id

    block_ref_map = task.get("block_ref_map", {})
    if isinstance(block_ref_map, dict):
        candidate = block_ref_map.get(str(block_ref))
        if isinstance(candidate, str) and candidate:
            return candidate
    return None


def _resolve_update_canvas_element_args(
    *,
    task: AgentTask,
    tool_args: dict[str, Any],
) -> tuple[dict[str, Any], str | None]:
    args = dict(tool_args)
    block_ref = args.pop("block_ref", None)
    if block_ref is None:
        return args, None

    element_id = _resolve_block_ref_from_task(task, block_ref)

    if not element_id:
        return args, _unknown_block_ref_result(block_ref, "update_canvas_element")

    args["element_id"] = element_id
    args["_block_ref"] = str(block_ref)
    return args, None


def _resolve_update_canvas_elements_args(
    *,
    state: dict[str, Any],
    task: AgentTask,
    tool_args: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    operations = tool_args.get("operations")
    if not isinstance(operations, list):
        return {
            "operations": [],
            "_batch_results": [
                {
                    "ok": False,
                    "error": "invalid_operations",
                    "message": "operations must be a list.",
                }
            ],
        }, []

    resolved_operations: list[dict[str, Any]] = []
    mutation_operations: list[dict[str, Any]] = []
    batch_results: list[dict[str, Any]] = []
    valid_element_ids = {
        block.block_id
        for block in _extract_moss_blocks(str(state.get("canvas_snapshot") or ""))
    }

    for index, operation in enumerate(operations):
        if not isinstance(operation, dict):
            batch_results.append(
                {
                    "ok": False,
                    "error": "invalid_operation",
                    "index": index,
                    "message": "operation must be an object.",
                }
            )
            continue

        block_ref = operation.get("block_ref")
        element_id = _resolve_block_ref_from_task(task, block_ref)
        action_type = operation.get("action_type", "replace")
        new_html = operation.get("new_html", "")

        if not element_id:
            batch_results.append(
                {
                    "ok": False,
                    "error": "unknown_block_ref",
                    "block_ref": block_ref,
                    "action_type": action_type,
                    "message": "block_ref is not available in the current canvas context.",
                    "hint": "Use the exact block_ref shown in canvas_context, such as b1 or b2.",
                }
            )
            continue

        if element_id not in valid_element_ids:
            batch_results.append(
                {
                    "ok": False,
                    "error": "element_id_not_found",
                    "block_ref": str(block_ref),
                    "action_type": action_type,
                    "message": "element_id does not exist in current canvas_snapshot.",
                    "hint": "Use the exact block_ref shown in canvas_context, such as b1 or b2.",
                }
            )
            continue

        resolved_operation = {
            "block_ref": str(block_ref),
            "element_id": element_id,
            "action_type": action_type,
            "new_html": new_html,
        }
        resolved_operations.append(resolved_operation)
        mutation_operations.append(resolved_operation)
        batch_results.append(
            {
                "ok": True,
                "block_ref": str(block_ref),
                "action_type": action_type,
            }
        )

    return {
        "operations": resolved_operations,
        "_batch_results": batch_results,
    }, mutation_operations


def _resolve_canvas_read_args(
    *,
    task: AgentTask,
    tool_name: str,
    tool_args: dict[str, Any],
) -> tuple[dict[str, Any], str | None]:
    args = dict(tool_args)
    block_ref = args.pop("anchor_block_ref", None)
    anchor_block_id = args.get("anchor_block_id")
    if block_ref is None and is_block_ref(anchor_block_id):
        block_ref = args.pop("anchor_block_id", None)
    if block_ref is None:
        return args, None

    anchor_block_id = _resolve_block_ref_from_task(task, block_ref)
    if not anchor_block_id:
        return args, _unknown_block_ref_result(block_ref, tool_name)

    args["anchor_block_id"] = anchor_block_id
    return args, None


def _redact_update_canvas_result_for_llm(result_str: str, block_ref: str | None) -> str:
    if not block_ref:
        return result_str
    try:
        payload = json.loads(result_str)
    except json.JSONDecodeError:
        return result_str
    if not isinstance(payload, dict):
        return result_str

    payload["block_ref"] = block_ref
    payload.pop("element_id", None)
    return json.dumps(payload, ensure_ascii=False)


def _redact_canvas_context_add_result_for_llm(
    *,
    task: AgentTask,
    result_str: str,
) -> str:
    try:
        payload = json.loads(result_str)
    except json.JSONDecodeError:
        return result_str
    if not isinstance(payload, dict) or payload.get("operation") != "canvas_context_add":
        return result_str

    context_blocks = task.get("canvas_context_blocks", [])
    if not isinstance(context_blocks, list):
        context_blocks = []
    blocks_by_id = {
        str(block.get("block_id")): block
        for block in context_blocks
        if block.get("block_id")
    }

    anchor_block_ref = block_ref_for_block_id(
        context_blocks,
        payload.get("anchor_block_id"),
    )
    if anchor_block_ref:
        payload["anchor_block_ref"] = anchor_block_ref
    payload.pop("anchor_block_id", None)
    payload.pop("anchor_index", None)

    redacted_blocks: list[dict[str, Any]] = []
    for raw_block in payload.get("blocks") or []:
        if not isinstance(raw_block, dict):
            continue
        block_id = str(raw_block.get("block_id") or "")
        context_block = blocks_by_id.get(block_id, raw_block)
        block_ref = str(context_block.get("block_ref") or "") or block_ref_for_block_id(
            context_blocks,
            block_id,
        )
        redacted_blocks.append(
            {
                "block_ref": block_ref,
                "tag": raw_block.get("tag", context_block.get("tag", "unknown")),
                "heading_path": raw_block.get("heading_path", context_block.get("heading_path", [])),
                "text": raw_block.get("text", context_block.get("text", "")),
                "html": strip_moss_block_id(
                    str(raw_block.get("html") or context_block.get("html") or "")
                ),
            }
        )
    payload["blocks"] = redacted_blocks

    return json.dumps(payload, ensure_ascii=False)


def _run_tools_for_current_task(state: dict[str, Any]) -> tuple[list[AgentTask], list[dict]]:
    current_idx = state["current_task_index"]
    tasks = list(state["tasks"])
    task = tasks[current_idx]
    task_messages = list(task.get("task_message", []))
    last_msg = task_messages[-1]

    tool_results: list[ToolMessage] = []
    pending_mutations: list[dict] = []

    for tool_call in last_msg.tool_calls:
        tool_name = tool_call["name"]
        tool_args = dict(tool_call.get("args") or {})
        tool_context = {
            **_trace_context(state, task),
            "tool_call_id": tool_call.get("id"),
        }
        visible_tool_context = _visible_trace_fields(tool_context)
        started_at = perf_counter()
        tool = next(
            (t for t in DOCUMENT_TOOLS if t.name == tool_name),
            None,
        )
        if not tool:
            result_str = f"Tool '{tool_name}' not found."
            log_tool_result(
                tool_name,
                result_str,
                duration_ms=round((perf_counter() - started_at) * 1000),
                raw_payload={
                    "args": tool_args,
                    "result": result_str,
                    **tool_context,
                },
                **visible_tool_context,
            )
            tool_results.append(
                ToolMessage(
                    content=result_str,
                    tool_call_id=tool_call["id"],
                )
            )
            continue

        try:
            task, budget_result = _consume_tool_budget(
                state=state,
                task=task,
                tool_name=tool_name,
            )
            if budget_result is not None:
                result_str = budget_result
                log_tool_result(
                    tool_name,
                    result_str,
                    duration_ms=round((perf_counter() - started_at) * 1000),
                    raw_payload={
                        "args": tool_args,
                        "result": result_str,
                        **tool_context,
                    },
                    **visible_tool_context,
                )
                tool_results.append(
                    ToolMessage(content=result_str, tool_call_id=tool_call["id"])
                )
                continue

            args = dict(tool_args)
            resolved_block_ref: str | None = None
            batch_mutation_operations: list[dict[str, Any]] = []
            if tool_name == "update_canvas_element":
                args, ref_error = _resolve_update_canvas_element_args(
                    task=task,
                    tool_args=tool_args,
                )
                if ref_error is not None:
                    result_str = ref_error
                    log_tool_result(
                        tool_name,
                        result_str,
                        duration_ms=round((perf_counter() - started_at) * 1000),
                        raw_payload={
                            "args": tool_args,
                            "result": result_str,
                            **tool_context,
                        },
                        **visible_tool_context,
                    )
                    tool_results.append(
                        ToolMessage(content=result_str, tool_call_id=tool_call["id"])
                    )
                    continue
                resolved_block_ref = args.pop("_block_ref", None)
            elif tool_name == "update_canvas_elements":
                args, batch_mutation_operations = _resolve_update_canvas_elements_args(
                    state=state,
                    task=task,
                    tool_args=tool_args,
                )
            elif tool_name in {"canvas_read_before", "canvas_read_after"}:
                args, ref_error = _resolve_canvas_read_args(
                    task=task,
                    tool_name=tool_name,
                    tool_args=tool_args,
                )
                if ref_error is not None:
                    result_str = ref_error
                    log_tool_result(
                        tool_name,
                        result_str,
                        duration_ms=round((perf_counter() - started_at) * 1000),
                        raw_payload={
                            "args": tool_args,
                            "result": result_str,
                            **tool_context,
                        },
                        **visible_tool_context,
                    )
                    tool_results.append(
                        ToolMessage(content=result_str, tool_call_id=tool_call["id"])
                    )
                    continue
            if tool_name in STATEFUL_DOCUMENT_TOOL_NAMES:
                args["state"] = state
            if tool_name in {"update_canvas_element", "update_canvas_elements"} and hasattr(tool, "func"):
                result = tool.func(**args)
            else:
                result = tool.invoke(args)
            result_str = str(result) if result is not None else ""
            if tool_name == "update_canvas_element":
                result_str = _redact_update_canvas_result_for_llm(
                    result_str,
                    resolved_block_ref,
                )
            log_tool_result(
                tool_name,
                _trace_payload(result),
                duration_ms=round((perf_counter() - started_at) * 1000),
                raw_payload={
                    "args": tool_args,
                    "result": _trace_payload(result),
                    **tool_context,
                },
                **visible_tool_context,
            )
            task = _apply_canvas_context_tool_result(
                state=state,
                task=task,
                result_str=result_str,
            )
            result_str = _redact_canvas_context_add_result_for_llm(
                task=task,
                result_str=result_str,
            )
        except Exception as e:
            result_str = f"Tool error: {e}"
            log_agent_error("tool invoke failed", e, tool=tool_name, **tool_context)
            log_tool_result(
                tool_name,
                result_str,
                duration_ms=round((perf_counter() - started_at) * 1000),
                error=str(e),
                raw_payload={
                    "args": tool_args,
                    "result": result_str,
                    "error": str(e),
                    **tool_context,
                },
                **visible_tool_context,
            )

        tool_results.append(
            ToolMessage(content=result_str, tool_call_id=tool_call["id"])
        )

        # Capture DOM mutations only after update_canvas_element validates the target.
        if tool_name == "update_canvas_element":
            try:
                mutation_payload = json.loads(result_str)
            except json.JSONDecodeError:
                mutation_payload = {}
            if isinstance(mutation_payload, dict) and mutation_payload.get("ok") is True:
                pending_mutations.append({
                    "element_id": args.get("element_id", ""),
                    "action_type": args.get("action_type", ""),
                    "new_html": args.get("new_html", ""),
                })
        elif tool_name == "update_canvas_elements":
            try:
                mutation_payload = json.loads(result_str)
            except json.JSONDecodeError:
                mutation_payload = {}
            if isinstance(mutation_payload, dict) and mutation_payload.get("applied_count", 0) > 0:
                for operation in batch_mutation_operations:
                    pending_mutations.append({
                        "element_id": operation.get("element_id", ""),
                        "action_type": operation.get("action_type", ""),
                        "new_html": operation.get("new_html", ""),
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
        return "finalize"
    task = tasks[current_idx]
    msgs = task.get("task_message", [])
    if msgs and getattr(msgs[-1], "tool_calls", None):
        return "tools"
    return "finalize"


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

    output = {"messages": messages, "pending_mutations": pending_mutations}
    trace_messages = _trace_messages(messages)
    log_node_exit(
        "reduce",
        {
            "response": trace_messages,
            "mutations": pending_mutations,
        },
        raw_payload={
            "messages": trace_messages,
            "pending_mutations": pending_mutations,
            "request_id": request_id,
        },
    )
    return output


# ── Graph Definition ─────────────────────────────────────────────────────

worker_builder = StateGraph(TaskWorkerState, output=TaskWorkerOutputState)
worker_builder.add_node("execute", aexecute_task_node)
worker_builder.add_node("tools", worker_tools_node)
worker_builder.add_node("finalize", worker_finalize_node)
worker_builder.add_edge(START, "execute")
worker_builder.add_edge("tools", "execute")
worker_builder.add_edge("finalize", END)
worker_builder.add_conditional_edges(
    "execute",
    router_execute_task,
    {"tools": "tools", "finalize": "finalize"},
)
task_worker_graph = worker_builder.compile()


async def task_worker_node(state: TaskWorkerState) -> TaskWorkerOutputState:
    """Run the worker subgraph and expose only parent-safe result channels."""
    output = await task_worker_graph.ainvoke(state)
    return {"task_results": list(output.get("task_results", []))}


builder = StateGraph(AgentState)
builder.add_node("intent", aintent_node)
builder.add_node("task_assemble", task_assemble_node)
builder.add_node("task_worker", task_worker_node)
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
    log_user_input(
        user_input,
        raw_payload={
            "session_id": session_id,
            "conversation_id": conversation_id,
            "request_id": initial_state["request_id"],
            "focus_element_id": focus_element_id,
            "focus_block_id": focus_block_id,
            "canvas_snapshot": canvas_snapshot,
            "canvas_snapshot_length": len(canvas_snapshot),
        },
    )

    runtime_graph = compiled_graph or graph
    settings = get_settings()
    config = {
        "configurable": {"thread_id": conversation_id},
        "recursion_limit": settings.agent_recursion_limit,
    }

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
