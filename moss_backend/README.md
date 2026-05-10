# Moss Backend

FastAPI backend for Moss. It serves the single-file frontend, exposes document APIs, and runs the LangGraph-based assistant behind the `/api/v1/chat-stream` SSE endpoint.

## Run

```powershell
cd moss_backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Open:

```text
http://127.0.0.1:8000/
```

## Configuration

Settings are loaded from `app/core/.env`, `moss_backend/.env`, or the workspace `.env`.

```env
ENABLE_MOCK_LLM=true
LLM_API_KEY=
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL=deepseek-chat
LLM_TEMPERATURE=0.2
STORAGE_DIR=storage
CONVERSATION_METADATA_DB=
LANGGRAPH_CHECKPOINT_DB=
```

`ENABLE_MOCK_LLM=true` keeps the app usable without an LLM key. In mock mode the graph returns a canned chat response and skips real intent classification and tool calls. Set it to `false` and fill `LLM_API_KEY` to exercise document QA, edit tools, and `dom_mutation` events with a real OpenAI-compatible model.

When omitted, `CONVERSATION_METADATA_DB` resolves to `storage/conversations.sqlite3` and `LANGGRAPH_CHECKPOINT_DB` resolves to `storage/langgraph_checkpoints.sqlite3`.

## API Surface

### `GET /api/v1/health`

Returns:

```json
{ "status": "ok", "service": "moss-backend" }
```

### `POST /api/v1/chat-stream`

Canonical payload:

```json
{
  "session_id": "session-123",
  "conversation_id": "conv-clientGenerated123",
  "user_input": "帮我润色这段",
  "focus_element_id": "moss-block-abc",
  "focus_block_id": "moss-block-abc",
  "canvas_snapshot": "<p id=\"moss-block-abc\">...</p>"
}
```

The schema also accepts the older `message/context.documentHTML/context.cursorPosition` shape and normalizes it internally.

`conversation_id` is optional. If omitted, the backend creates a conversation for the fixed development user `test-user` and uses that id as the LangGraph `thread_id`. `session_id` remains a browser/session marker and does not control thread recovery.

SSE events emitted by the route:

- `conversation`: emitted when the backend creates conversation metadata; data includes `conversation_id` and `user_id`.
- `node_start` / `node_end`: graph progress for `intent`, `task_assemble`, `execute`, `tools`, and `task_advance`.
- `chat_chunk`: assistant text, currently sent as complete message chunks with `done: true`.
- `dom_mutation`: structured document edit emitted when `update_canvas_element` is called.
- `done`: request completed.
- `error`: request failed.

Example `conversation` data:

```json
{
  "conversation_id": "conv-clientGenerated123",
  "user_id": "test-user"
}
```

Example `dom_mutation` data:

```json
{
  "element_id": "moss-block-abc",
  "action_type": "replace",
  "new_html": "<p id=\"moss-block-abc\">新的段落内容</p>"
}
```

### `POST /api/document/upload`

Multipart upload under field `file`. Supports `.txt`, `.md`, `.markdown`, `.docx`, and `.pdf`. The parser returns plain text plus HTML, and wraps top-level blocks with generated `moss-block-*` IDs when needed.

Response:

```json
{
  "status": "success",
  "filename": "demo.md",
  "textContent": "...",
  "htmlContent": "<div id=\"moss-block-...\"><p>...</p></div>"
}
```

`POST /api/v1/upload` remains as a compatibility endpoint with `text` instead of `textContent`.

### `POST /api/document/save`

Writes HTML and metadata to `storage/documents/`.

```json
{
  "docId": "doc-current",
  "content": "<p>...</p>",
  "timestamp": 1714400000
}
```

### `POST /api/document/export`

Exports the supplied HTML as `markdown`, `html`, or `pdf`.

```json
{
  "format": "markdown",
  "content": "<h1>Title</h1>",
  "filename": "moss-document"
}
```

PDF export imports `weasyprint` lazily and returns `501` if that optional dependency is not installed.

## Agent Flow

`app/agent/graph.py` defines a LangGraph pipeline:

```text
START -> intent -> task_assemble -> execute
                                  -> tools -> execute
                                  -> task_advance -> execute | END
```

- `intent`: classifies the request as `general_chat`, `document_qa`, `local_edit`, or `global_edit`. Mock mode skips this and returns `general_chat`.
- `task_assemble`: selects prompt templates, trims document context, and chooses allowed tools.
- `execute`: calls the configured chat model and binds only the tools allowed for the task.
- `tools`: executes document tools and captures `update_canvas_element` calls as pending DOM mutations.
- `stream_agent_events`: converts graph events into SSE frames consumed by the frontend.

## Document Tools

- `search_document_blocks`: parses `canvas_snapshot`, builds a lightweight outline, ranks matching `moss-block-*` blocks, and optionally returns block HTML for edit planning.
- `update_canvas_element`: does not edit server-side HTML. It returns a tool result and lets the route forward the mutation to the browser as `dom_mutation`.
- `generate_download_link`: stores an in-memory export artifact in `DOWNLOAD_CACHE` and returns `/api/v1/download/{token}`.

## Tests

```powershell
cd moss_backend
python -m unittest discover -s tests -v
```

Full discovery is not green in the current repo state:

- `test_skill_runtime.py` and `test_agent_refactor.py` target the planned `app.agent.skill_runtime` refactor, which is not implemented yet.
- `test_document_content.py::test_rejects_unsupported_task_type` still expects `document_qa` to be rejected, while the current `tailor_context()` implementation allows it.

These failures reflect test/implementation drift, not the FastAPI runtime entrypoint.
