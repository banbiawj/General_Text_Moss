# Moss 智能文档助手

此目录是根据 `Blueprint/SYSTEM_DESIGN.md` 生成的实际工程代码。`Blueprint` 仍作为只读参考资料保留，运行代码位于根目录和 `moss_backend/`。

## 结构

```text
index.html                 # Vue 3 + Tiptap + Tailwind 单文件前端
moss_backend/
  app/
    api/                   # FastAPI 路由与 Pydantic 契约
    agent/                 # LangGraph ReAct 状态机
    tools/                 # 文档修改/下载工具
    services/              # 文件解析服务
    core/                  # 配置
    main.py                # FastAPI 入口
  requirements.txt
```

## 启动

```powershell
cd moss_backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy app\core\.env.example app\core\.env
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

浏览器访问：

```text
http://127.0.0.1:8000/
```

默认 `ENABLE_MOCK_LLM=true`，无需大模型密钥即可跑通 SSE、聊天流和 `dom_mutation` 文档局部更新链路。接入真实模型时，在 `app/core/.env` 中填入 `LLM_API_KEY`，并将 `ENABLE_MOCK_LLM=false`。

## 大模型日志

真实模型调用时，后端会把每条发送给模型和模型返回的消息写入 JSONL：

```text
moss_backend/storage/logs/llm_messages.jsonl
```

每行包含 `timestamp`、`session_id`、`request_id`、`llm_call_id`、`direction`、`sender`、`message_type`、`content` 等字段。可通过 `ENABLE_LLM_LOGGING=false` 关闭，或用 `LLM_LOG_FILE` 自定义相对 `STORAGE_DIR` 的日志文件路径。
