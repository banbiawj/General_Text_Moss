# Moss 智能文档助手

Moss 是一个“富文本编辑器 + 上下文 AI 伴写”的本地 Web 应用。当前项目已经从早期蓝图推进到可运行形态：根目录的 `index.html` 是前端入口，`moss_backend/` 提供 FastAPI 后端、LangGraph Agent、文档导入导出和 SSE 事件流。

`Blueprint/SYSTEM_DESIGN.md` 现在作为当前系统设计说明保留；`docs/superpowers/plans/` 中的文件是开发计划或历史方案，不等同于已实现功能。

## 当前能力

- 单文件前端：Vue 3 + Tiptap + Tailwind CDN，直接由后端 `/` 路由托管。
- 沉浸式编辑：支持标题快捷键、全屏、拖拽调整编辑区域、保存、导入和导出。
- 文档定位：前端自动维护顶层 `moss-block-*` ID，并随请求发送 `focus_element_id`、`focus_block_id` 和完整 HTML 快照。
- AI 交互：底部全局输入框和 `Ctrl + /` 局部伴写都会调用 `/api/v1/chat-stream`。
- SSE 协议：后端返回 `chat_chunk` 聊天文本，也可以通过 `dom_mutation` 下发局部文档修改指令。
- 文件处理：支持 `.txt`、`.md`、`.markdown`、`.docx`、`.pdf` 导入；支持 Markdown、HTML 导出，PDF 导出需要额外安装 `weasyprint`。
- 后端 Agent：根据请求意图分为普通聊天、文档问答、局部编辑、全文编辑，并按任务类型裁剪上下文和暴露工具。

## 项目结构

```text
index.html                         # Vue 3 + Tiptap 单文件前端
Blueprint/SYSTEM_DESIGN.md          # 当前系统设计说明
docs/superpowers/plans/             # 开发计划与历史方案
moss_backend/
  app/
    api/
      routes.py                     # FastAPI 路由、SSE、上传/保存/导出
      schemas.py                    # Pydantic 请求/响应模型
    agent/
      graph.py                      # LangGraph 节点、路由和 SSE 事件转发
      state.py                      # AgentState 与 AgentTask
      prompts/                      # 意图识别与任务提示词模板
    tools/
      document_tools.py             # 文档检索、DOM 修改、下载链接工具
    services/
      document_content.py           # HTML 块抽取与上下文裁剪
      file_parser.py                # 上传文件解析与块 ID 补齐
    core/
      config.py                     # 环境变量与运行配置
      llm_logging.py                # JSONL 日志工具，当前未接入 graph 调用链
    main.py                         # FastAPI 应用入口，同时托管 index.html
  tests/                            # 后端测试；部分 skill_runtime 用例对应计划中功能
  requirements.txt
```

## 启动

```powershell
cd moss_backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

浏览器访问：

```text
http://127.0.0.1:8000/
```

前端依赖通过 CDN 加载，因此浏览器运行页面时需要能访问 Vue、Tiptap、Tailwind、Font Awesome 等 CDN。

## 环境变量

配置会从以下位置读取。为避免同名变量覆盖带来的混淆，建议同一环境只维护其中一个 `.env` 文件，并让系统环境变量承载 CI 或部署环境的覆盖值：

```text
moss_backend/app/core/.env
moss_backend/.env
.env
```

最小可选配置示例：

```env
ENABLE_MOCK_LLM=true
LLM_API_KEY=
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL=deepseek-chat
LLM_TEMPERATURE=0.2
STORAGE_DIR=storage
```

默认 `ENABLE_MOCK_LLM=true`。Mock 模式可以跑通前后端连接和 SSE 基础链路，但不会调用真实模型，也不会产生真实工具调用。需要验证意图识别、文档问答、局部修改和 `dom_mutation` 时，应配置真实 OpenAI 兼容模型并设置：

```env
ENABLE_MOCK_LLM=false
LLM_API_KEY=你的密钥
```

## 常用接口

- `GET /api/v1/health`：健康检查。
- `POST /api/v1/chat-stream`：AI 对话与文档修改 SSE 入口。
- `POST /api/v1/upload`：兼容旧前端的上传接口，返回 `filename`、`text`、`htmlContent`。
- `GET /api/v1/download/{token}`：工具生成的临时下载链接。
- `POST /api/document/upload`：当前前端使用的上传接口，返回 `filename`、`textContent`、`htmlContent`。
- `POST /api/document/save`：保存当前 HTML 到 `storage/documents/`。
- `POST /api/document/export`：导出 Markdown、HTML 或 PDF。

## 验证

文档变更可先运行：

```powershell
git diff --check
```

后端测试入口：

```powershell
cd moss_backend
python -m unittest discover -s tests -v
```

注意：当前完整测试发现与实现状态不完全一致，已知会失败：

- `tests/test_skill_runtime.py` 和 `tests/test_agent_refactor.py` 描述的是 `docs/superpowers/plans/2026-05-05-skill-runtime.md` 中的计划性重构；`app.agent.skill_runtime` 尚未实现。
- `tests/test_document_content.py::test_rejects_unsupported_task_type` 仍按旧逻辑期待 `document_qa` 被拒绝，但当前 `tailor_context()` 已允许 `document_qa`。

这些问题不影响 FastAPI 运行入口，但后续整理测试时需要同步处理。
