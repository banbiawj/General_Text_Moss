一、 工程目录结构定义
采用标准的现代 Python 后端分层架构，保持极简与高内聚。
moss_backend/
├── app/
│   ├── api/
│   │   ├── routes.py          # FastAPI 路由层（处理 HTTP / SSE 请求）
│   │   └── schemas.py         # Pydantic 数据校验模型（Request/Response）
│   ├── agent/
│   │   ├── graph.py           # LangGraph 核心状态机与图定义
│   │   ├── state.py           # AgentState 状态类定义
│   │   └── prompt.py          # System Prompt 模板管理
│   ├── tools/
│   │   └── document_tools.py  # 所有的 @tool 技能（修改、下载等）
│   ├── services/
│   │   └── file_parser.py     # 文件上传解析服务（PDF/Word转Text）
│   ├── core/
│   │   ├── .env
│   │   └── config.py          # 环境变量与配置 (API Keys, 数据库URL)
│   └── main.py                # FastAPI 应用入口与中间件配置
└── requirements.txt
一、 app/api/ (通信与契约层)
这一层是后端与 Vue 前端的唯一物理接触点，负责数据校验和协议转换。

1. schemas.py (数据校验模型)
使用 Pydantic 定义严格的数据进出规范。

ChatRequest (入参模型)：

session_id (字符串): 标识当前会话，后续扩展记忆模块（Checkpointer）的依据。

user_input (字符串): 用户的自然语言指令（例如：“帮我把个人项目经历这段精简一下”）。

focus_element_id (字符串，可选): 当前前端光标或鼠标选中的 DOM 节点 ID（例如："project-experience"），用于局部手术刀定位。

canvas_snapshot (字符串): 前端在触发请求瞬间抓取的完整、最新的 HTML 字符串。

UploadResponse (出参模型)：

定义文件上传成功后的标准结构，包含文件名、提取出的纯文本内容以及状态信息。

2. routes.py (路由控制器)
暴露两个核心 HTTP 接口，承担“信使”职责。

POST /api/v1/chat-stream (流式中枢)：

核心动作：接收 ChatRequest，将其转化为 LangGraph 的初始状态（State），并触发图（Graph）的执行。

数据流出：拦截 LangGraph 的底层事件流。

当监听到大模型输出普通文本时，封装为 event: chat_chunk 的 SSE 格式返回前端。

当监听到大模型调用了 update_canvas_element 工具时，立即截获其参数（目标 ID、修改动作、新 HTML），将其封装为 event: dom_mutation 的 SSE 格式下发，要求前端立刻执行 DOM 替换。

POST /api/v1/upload (传统接口)：

接收前端通过 FormData 传来的文件（如用户旧版的 PDF 文件），调用 services 层的解析器提取文本并返回，供前端作为下一次对话的上下文材料。

二、 app/agent/ (大脑与状态流转层)
系统的核心引擎，纯粹基于 LangGraph 的有向图与 ReAct 思想构建。

1. state.py (状态定义)
定义 Graph 在节点间传递的数据载体（基于 TypedDict）。

状态字段：

messages: 核心对话流，使用 operator.add 保证每一次大模型回复和工具执行结果都能追加到列表中。

canvas_snapshot: 每次请求带来的绝对事实，在整个单次图执行生命周期中作为只读常量存在。

focus_element_id: 当前局部上下文的焦点锚点。

2. prompt.py (系统提示词模板)
负责动态组装注入给大模型的“思想钢印”。

逻辑描述：该模块对外提供一个组装函数。每次请求时，该函数会将 state 中的 canvas_snapshot 和 focus_element_id 无缝拼接进预设的指令模板中。

指令约束：模板内明确规定大模型的行为底线——“你只能依据提供的快照回答问题；如果收到修改排版、增删内容的指令，绝对禁止在对话流中直接输出 HTML 代码，必须且只能触发对应的工具进行精确打击。”

3. graph.py (状态机编排)
定义 ReAct 循环，将所有零件组装成一部运转的机器。

初始化阶段：实例化兼容 OpenAI 接口的大模型对象（如 DeepSeek），并将 tools 目录下的工具列表绑定（bind_tools）给该模型。

节点定义 (Nodes)：

Agent Node：接收当前 State，利用 prompt.py 生成的上下文唤醒大模型进行推理，将大模型的输出（可能是文本，也可能是工具调用请求）附加到 State 的 messages 中。

Tool Node：LangGraph 内置节点，负责解析大模型的工具调用请求，实际执行对应的 Python 工具函数。

边定义 (Edges)：

设置条件路由（Conditional Edge）：每次 Agent Node 执行完后进行判断。如果大模型的回复中包含了工具调用指令，则将状态流转到 Tool Node；如果没有，则走向结束节点（END）。

设置闭环路由：Tool Node 执行完毕后，必须无条件回到 Agent Node，让大模型知道工具已经执行成功，以便其进行后续总结或继续执行。

三、 app/tools/ (技能与动作层)
独立解耦的工具箱，MVP 阶段包含两个核心基础工具。后续增添新技能完全不影响主架构。

1. document_tools.py (核心文档操作工具)
通过 @tool 装饰器定义，强制声明明确的参数 Schema 供大模型理解。

update_canvas_element (局部手术刀)：

参数约束：要求大模型必须提供 element_id（往哪里动刀）、action_type（替换/追加/删除）、new_html（新内容）。

执行逻辑：在后端，这个函数实际上什么 DOM 也不修改（因为真实 DOM 在前端）。它的唯一作用是返回一句系统提示词（如：“指令已成功派发至前端”），从而满足 LangGraph 的闭环要求。真正的修改动作，已经在 routes.py 监听此工具触发时，通过 SSE 提前派发给前端了。

generate_download_link (下载预备)：

参数约束：要求大模型提供用户想下载的格式（如 markdown 或 pdf）。

执行逻辑：生成一个 UUID 作为下载凭证，存入轻量级缓存，并组装出一个完整的下载 URL 返回。大模型随后会将这个 URL 通过聊天框发给用户。

四、 app/services/, app/core/, main.py (基础设施层)
外围支撑模块，确保系统的稳定运行与扩展。

1. app/services/file_parser.py (解析服务)
隔离所有与大模型无关的业务逻辑。

包含具体的提取函数（如利用 pdfplumber 或 python-docx 提取文本），对入参的二进制流进行清洗并返回纯文本，以确保未来可以平滑接入更复杂的 RAG 向量化预处理。

2. app/core/config.py (配置管理)
利用 Pydantic 的 BaseSettings 统一管理环境变量。

集中存储大模型 API Key、Base URL、允许跨域的前端地址列表等，避免硬编码。

3. main.py (应用入口)
初始化 FastAPI 实例。

至关重要：配置 CORS（跨域资源共享）中间件，允许 Vue 前端所在的端口进行访问。

挂载 app/api/routes.py 中的路由集合，启动 ASGI 服务器监听。


#前端介绍:
Moss 智能文档助手 - 前后端协同开发指南
🌟 1. 前端架构与核心能力概述
Moss 前端采用 单文件组件（SFC）融合模式，无须复杂构建工具，开箱即用。核心逻辑围绕“沉浸式编辑 + 上下文 AI 伴写”展开。

技术栈
核心框架：Vue 3 (Composition API, ESM 引入)

样式驱动：Tailwind CSS (CDN 实时编译)

富文本引擎：Tiptap (Headless 架构，底层为 ProseMirror)

图标库：FontAwesome 6.4

核心功能与 UX 设计
Tiptap 强定制富文本引擎：

支持原生级快捷键（Ctrl+1~6 切换标题，Ctrl++/- 升降级）。

核心突破：自定义了 CustomDiv 和 GlobalAttributes 扩展，打破了 Tiptap 默认的安全过滤，强制保留了 DOM 的 id、class 等属性。 这为 AI 精准定位和修改文档节点提供了坚实的基础。

双模式 AI 交互：

全局模式：底部固定输入框，用于全局指令或闲聊。

伴写模式 (Copilot)：按下 Ctrl + / 在光标处唤起悬浮输入窗，实现上下文感知的精准指令。

响应式空间管理：

支持 Ctrl + Shift + F 全屏沉浸写作。

支持自由拖拽中轴手柄（底边）调整编辑面板高度，并包含防误触（选中文本）机制。

底层视觉反馈链路：

封装了 scrollToTarget(targetId)（平滑滚动定位）和 highlightTarget(targetId)（基于底层 Transaction 机制的高亮闪烁），专供大模型回调使用。

🔌 2. 交互链路与后端接口规范 (Backend API Spec)
为了让 Moss 真正“活”起来，后端需要提供以下几组核心接口。强烈建议 AI 对话接口采用 SSE (Server-Sent Events) 或 WebSocket 以实现流式输出。

API 1: 核心 AI 对话与文档修改接口 (Chat & Copilot)
这是整个应用的心脏。前端的全局输入框和悬浮输入框都将调用此接口。

协议：WebSocket 或 HTTP SSE (流式响应)

前端发送 (Request Payload)：

JSON
{
  "message": "帮我把第二段优化得更正式一些",
  "context": {
    "documentHTML": "<h1>...</h1><div id='target-section'>...</div>", 
    "cursorPosition": "target-section", // 如果是通过 Ctrl+/ 唤起，可附带当前光标所在的节点 ID 或文本片段
    "history": [ ... ] // 历史对话记录
  }
}
后端返回 (Response Stream/Payload)：
重点： 后端/大模型不仅要返回聊天话术，还需要返回结构化的文档操作指令。前端拿到指令后即可自动调用替换、滚动和高亮方法。

JSON
{
  "reply": "好的，已经为您将该段落优化为更正式的商业口吻。", 
  "action": {
    "type": "replace", // 可选: replace, insert, delete
    "targetId": "target-section", // 告诉前端要操作哪个 DOM 节点
    "newHTML": "<h3>段落二：AI 互动演示</h3><p>已完成优化，文本更加流畅且符合商业规范。</p>" // 渲染的新内容
  }
}
前端收到响应后的处理伪代码（已在现有代码中预留位置）：

JavaScript
// 1. 将新内容同步给 Tiptap
contentHTML.value = contentHTML.value.replace(oldHTML, response.action.newHTML);
tiptapEditor.commands.setContent(contentHTML.value);

// 2. 触发视觉反馈
scrollToTarget(response.action.targetId);
highlightTarget(response.action.targetId);
API 2: 文档保存/同步接口
响应前端的 Ctrl + S 或右下角保存按钮。

Endpoint: /api/document/save (POST)

请求体 (Request)：

JSON
{
  "docId": "doc-12345",
  "content": "<h1>...完整HTML内容...</h1>",
  "timestamp": 1714400000
}
响应 (Response)：

JSON
{ "status": "success", "message": "Saved successfully" }
API 3: 文档解析与上传接口 (Upload)
响应顶部的 "Upload" 按钮，允许用户导入本地文件并转换为 Tiptap 可识别的 HTML。

Endpoint: /api/document/upload (POST, multipart/form-data)

参数: file (支持 .docx, .md, .txt)

后端处理要求：后端需要将 Word 或 Markdown 转换为标准的 HTML。关键要求：为了配合 Moss 的局部修改能力，后端解析时最好能为不同的块级元素（如段落、章节）自动生成唯一的 id（例如 <div id="block-uuid">...</div>），然后再返回给前端。

响应 (Response)：

JSON
{
  "status": "success",
  "htmlContent": "<div id='b1'><h1>标题</h1></div>..."
}
API 4: 导出接口 (Export)
响应顶部的 "Export" 按钮。

Endpoint: /api/document/export (POST)

请求体 (Request)：

JSON
{
  "format": "pdf", // 或 "markdown"
  "content": "<h1>...</h1>"
}
响应 (Response)：返回文件流 (Blob/File) 供浏览器下载。

🚀 3. 给 Vibe Coding 推进的建议 (Next Steps)
如果你接下来要使用 AI (比如 Cline, Cursor 等) 继续辅助开发，建议按照以下顺序喂给大模型 Prompt：

阶段一：Mock 接口联调 (Data Binding)

Prompt 建议：“基于现有的前端代码，请帮我引入 Axios (或 Fetch)，并将 sendMessage 函数改造为真实的 API 调用。请先在代码底部写一个 Mock 的异步函数来模拟后端返回结构化数据（包含 reply, targetId, newHTML），并把前端写死的 setTimeout 逻辑替换为处理这个 Mock 数据的逻辑。”

阶段二：SSE / 流式对话支持 (Streaming)

Prompt 建议：“现在我们要将普通的 HTTP 请求升级为 Server-Sent Events (SSE) 以支持打字机效果。请帮我改造 sendMessage，在接收流式数据时，动态更新 messages 数组中 AI 的最后一条回复，并在流结束时解析文档 Action JSON，触发 highlightTarget。”

阶段三：后端 Agent 逻辑搭建 (Backend Python/FastAPI)

Prompt 建议：“这是我们前端要求的数据协议（附上上面的 API 1 JSON）。我需要你用 Python FastAPI 写一个接口。当接收到用户的指令和当前文档 HTML 时，调用大模型（如 DeepSeek/GPT-4），利用 LangChain 或结构化输出，让大模型分析并返回符合要求的替换 HTML 和 Target ID。”