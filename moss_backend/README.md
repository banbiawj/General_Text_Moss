# Moss Backend

FastAPI + LangGraph backend for the Moss intelligent document assistant.

## Run

```powershell
cd moss_backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy app\core\.env.example app\core\.env
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

`ENABLE_MOCK_LLM=true` keeps the app runnable without an LLM key. Set it to
`false` and fill `LLM_API_KEY`, `LLM_BASE_URL`, and `LLM_MODEL` to use a real
OpenAI-compatible model.

## LLM JSONL logs

When real LLM calls are enabled, every message sent to the model and every
message returned by the model is appended to:

```text
storage/logs/llm_messages.jsonl
```

Each line includes `timestamp`, `session_id`, `request_id`, `llm_call_id`,
`direction`, `sender`, `message_type`, and `content`. Set
`ENABLE_LLM_LOGGING=false` to disable logging, or override `LLM_LOG_FILE` to
change the path relative to `STORAGE_DIR`.
