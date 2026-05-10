# Moss 系统设计

本文档描述当前仓库中的实际实现，而不是早期构想。运行入口位于根目录 `index.html` 与 `moss_backend/app/main.py`。

## 1. 产品目标

Moss 是一个智能文档助手：用户在一个沉浸式富文本编辑器里写作、导入文档、保存或导出内容，并通过全局输入框或光标处 `Ctrl + /` 指令让 AI 基于当前文档上下文回答问题或修改局部内容。

核心设计约束：

- 前端是当前文档 DOM 的事实来源。
- 后端只接收前端传来的 HTML 快照，不直接持有实时编辑器状态。
- AI 修改文档时必须输出结构化工具调用，由后端转成 `dom_mutation` SSE 事件交给前端执行。
- 大文档不能原样塞进模型上下文，后端需要根据任务类型裁剪或检索文档块。

## 2. 运行时架构

```text
Browser
  index.html
  Vue 3 + Tiptap editor
  moss-block-* IDs
  fetch SSE client
        |
        | HTTP / SSE
        v
FastAPI
  app.main
  api.routes
        |
        v
LangGraph Agent
  intent -> task_assemble -> execute -> tools -> execute -> task_advance
        |
        v
Document services/tools
  file_parser
  document_content
  document_tools
```

## 3. 前端设计

前端是根目录 `index.html`，没有独立构建系统。

技术栈：

- Vue 3 Composition API，通过 import map 从 CDN 加载。
- Tiptap 2 / ProseMirror 作为富文本编辑内核。
- Tailwind CDN 负责样式。
- Font Awesome 提供图标。

主要能力：

- 顶部工具栏：导入、导出、当前文档标题。
- 编辑面板：Tiptap 渲染和编辑文档内容。
- 底部对话框：发送全局指令到 `/api/v1/chat-stream`。
- 悬浮伴写框：`Ctrl + /` 在当前光标附近打开局部指令输入。
- 快捷键：`Ctrl + 1~6` 切换标题层级，`Ctrl + +/-` 升降标题，`Ctrl + S` 保存，`Ctrl + Shift + F` 全屏。
- 空间管理：编辑面板可拖拽调整高度，支持全屏沉浸模式。

### 文档 ID 策略

前端会维护顶层块 ID：

- 顶层可编辑块没有 ID 时，会生成 `moss-block-*`。
- 发送请求前会调用 `ensureTopLevelBlockIds()`，保证快照内有稳定锚点。
- 若当前选中节点没有 ID，会临时添加 `moss-temp-anchor-*`，请求结束后清理。
- 请求会携带 `focus_element_id` 和 `focus_block_id`，分别表示精确焦点节点和所属顶层块。

### 前端处理后端事件

`sendMessage()` 使用 `fetch()` 读取 SSE 流：

- `chat_chunk`：追加到最后一条 AI 消息。
- `dom_mutation`：调用 `applyDomMutation()` 修改本地 HTML，再同步回 Tiptap。
- `error`：显示服务端错误。
- 其他 `node_start` / `node_end` 事件当前主要用于调试和未来进度展示。

`applyDomMutation()` 支持：

- `replace`：替换目标节点；如果替换顶层块且新 HTML 没有保留原 ID，会把原 ID 放回第一个替换元素。
- `append`：追加到目标节点内部。
- `insert`：插入到目标节点之后；目标不存在时追加到根节点。
- `delete`：删除目标节点。

## 4. 后端 API

后端有两组路由：`/api/v1/*` 和 `/api/document/*`。

### 健康检查

```http
GET /api/v1/health
```

```json
{ "status": "ok", "service": "moss-backend" }
```

### AI 对话与文档修改

```http
POST /api/v1/chat-stream
Content-Type: application/json
Accept: text/event-stream
```

请求体：

```json
{
  "session_id": "session-123",
  "user_input": "帮我润色这段",
  "focus_element_id": "moss-block-abc",
  "focus_block_id": "moss-block-abc",
  "canvas_snapshot": "<p id=\"moss-block-abc\">原文</p>"
}
```

兼容旧形态：

```json
{
  "message": "帮我总结一下",
  "context": {
    "documentHTML": "<p id=\"moss-block-abc\">内容</p>",
    "cursorPosition": "moss-block-abc",
    "history": []
  }
}
```

SSE 事件：

- `node_start`：节点开始执行。
- `node_end`：节点执行结束，并返回可 JSON 序列化的节点输出。
- `chat_chunk`：返回聊天文本。
- `dom_mutation`：返回文档修改指令。
- `done`：请求正常结束。
- `error`：请求异常。

`dom_mutation` 结构：

```json
{
  "element_id": "moss-block-abc",
  "action_type": "replace",
  "new_html": "<p id=\"moss-block-abc\">修改后的内容</p>"
}
```

### 文件上传

当前前端使用：

```http
POST /api/document/upload
Content-Type: multipart/form-data
```

字段：`file`

支持：`.txt`、`.md`、`.markdown`、`.docx`、`.pdf`

响应：

```json
{
  "status": "success",
  "filename": "demo.md",
  "textContent": "纯文本",
  "htmlContent": "<div id=\"moss-block-...\"><p>HTML</p></div>"
}
```

兼容接口 `POST /api/v1/upload` 返回字段为 `text` 和 `htmlContent`。

### 保存文档

```http
POST /api/document/save
```

```json
{
  "docId": "doc-current",
  "content": "<p>完整 HTML</p>",
  "timestamp": 1714400000
}
```

后端写入：

```text
storage/documents/{docId}.html
storage/documents/{docId}.json
```

### 导出文档

```http
POST /api/document/export
```

```json
{
  "format": "markdown",
  "content": "<h1>Title</h1>",
  "filename": "moss-document"
}
```

支持格式：

- `markdown`：使用 `markdownify` 从 HTML 转换。
- `html`：原样导出 HTML。
- `pdf`：运行时尝试导入 `weasyprint`；未安装时返回 `501`。

### 临时下载

```http
GET /api/v1/download/{token}
```

该接口服务于 `generate_download_link` 工具。下载内容存放在进程内 `DOWNLOAD_CACHE`，服务重启后失效。

## 5. Agent 设计

Agent 位于 `moss_backend/app/agent/graph.py`。

状态字段由 `state.py` 定义，核心字段包括：

- `messages`：本轮图执行产生的全局消息轨迹。
- `user_input`：用户输入。
- `canvas_snapshot`：前端传来的完整 HTML 快照。
- `focus_element_id`：当前精确焦点节点。
- `focus_block_id`：当前焦点所属顶层块。
- `task_type` / `task_reason`：意图分类结果。
- `tasks`：本轮拆出的任务列表。
- `current_task_index`：当前任务下标。
- `pending_mutations`：待转发到前端的 DOM 修改指令。
- `session_id` / `request_id`：会话和请求标识。

### 节点流程

```text
START
  -> intent
  -> task_assemble
  -> execute
      -> tools
      -> execute
  -> task_advance
      -> execute 或 END
```

### `intent`

真实模型模式下使用 `intent_prompt.yaml` 和结构化输出将请求分为：

- `general_chat`：不依赖文档内容的普通聊天。
- `document_qa`：基于当前文档问答、总结、解释、提取信息。
- `local_edit`：局部修改、润色、扩写、删除、插入。
- `global_edit`：明确面向全文、整篇、全部章节的批量编辑。

Mock 模式下直接返回 `general_chat`。

### `task_assemble`

根据 `task_type` 选择提示词、上下文和工具：

```text
general_chat -> 无工具
document_qa  -> search_document_blocks
local_edit   -> search_document_blocks, update_canvas_element
global_edit  -> update_canvas_element
```

上下文裁剪由 `services/document_content.py` 完成：

- `local_edit`：围绕 `focus_block_id` 取前 3 块、当前块、后 3 块。
- `global_edit`：每 4 个 `moss-block-*` 切成一个任务片段。
- `document_qa`：当前实现会按焦点块裁剪；如果没有焦点块，则依赖 `search_document_blocks` 工具从完整快照中检索。

### `execute`

真实模型模式下创建 `ChatOpenAI`，绑定当前任务允许的工具，然后执行任务提示词。若模型返回工具调用，路由到 `tools`；否则把 AI 文本作为最终回复输出。

Mock 模式下返回固定文本：

```text
（Mock 回复）收到您的消息，当前任务类型已识别。
```

### `tools`

执行模型发起的工具调用：

- 调用 `search_document_blocks` 时，从 `canvas_snapshot` 解析 `moss-block-*`，做关键词检索和邻近块返回。
- 调用 `update_canvas_element` 时，后端不修改 HTML，只把参数收集到 `pending_mutations`。
- `stream_agent_events()` 在 `tools` 节点结束时把 `pending_mutations` 转成 `dom_mutation` SSE 事件。

## 6. 文档内容处理

### 上传解析

`services/file_parser.py` 根据扩展名选择解析方式：

- `.txt`：UTF-8 文本，按空行转段落。
- `.md` / `.markdown`：使用 `markdown` 转 HTML。
- `.docx`：使用 `python-docx` 读取段落和 Heading 样式。
- `.pdf`：使用 `pdfplumber` 提取文本，再转段落 HTML。

解析后的 HTML 会经过 `ensure_block_ids()`：

- 对顶层 `p`、`h1`-`h6`、`ul`、`ol`、`blockquote` 等块级元素包一层 `div id="moss-block-*"`。
- 顶层 `div` 如果没有 ID，会补上 `moss-block-*`。

### 快照抽取

`services/document_content.py` 使用 `HTMLParser` 抽取顶层 `moss-block-*` 块，保留原始 HTML 片段。该模块是局部编辑窗口和全文分批的基础。

`tools/document_tools.py` 也会基于相同的块抽取能力，进一步生成：

- 可见文本。
- 标题路径。
- 文档大纲。
- 邻近块 ID。
- 简单关键词评分。

## 7. 配置与存储

`core/config.py` 使用 Pydantic Settings，按以下路径加载配置：

```text
moss_backend/app/core/.env
moss_backend/.env
.env
```

主要变量：

```env
CORS_ORIGINS=http://localhost:8000,http://127.0.0.1:8000,http://localhost:5173,http://127.0.0.1:5173,null
ENABLE_MOCK_LLM=true
LLM_API_KEY=
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL=deepseek-chat
LLM_TEMPERATURE=0.2
ENABLE_LLM_LOGGING=true
LLM_LOG_FILE=logs/llm_messages.jsonl
STORAGE_DIR=storage
```

当前代码中 `core/llm_logging.py` 提供 JSONL 记录工具，但 `graph.py` 尚未调用它；因此不要把 `ENABLE_LLM_LOGGING` 视为已覆盖所有 LLM 请求的运行特性。

运行数据默认写入 `moss_backend/storage/`，该目录被 `.gitignore` 忽略。

## 8. 当前边界

- 前端是单文件 CDN 形态，没有打包、离线资源或组件拆分。
- 后端没有鉴权和用户隔离，适合本地开发或受控环境。
- `DOWNLOAD_CACHE` 是进程内内存缓存，服务重启会丢失。
- PDF 导出需要手动安装 `weasyprint`，不在当前 `requirements.txt` 中。
- `docs/superpowers/plans/2026-05-05-skill-runtime.md` 描述的是计划中的 skill runtime 重构；当前 `graph.py` 仍使用硬编码任务类型到工具的映射。
- `tests/test_skill_runtime.py` 与 `tests/test_agent_refactor.py` 已写入该计划的期望，当前实现未完成前，完整测试发现会报告相关导入失败。
- `tests/test_document_content.py::test_rejects_unsupported_task_type` 仍沿用旧预期，认为 `document_qa` 应被拒绝；当前实现已经允许 `document_qa`，因此该测试也需要后续同步。
