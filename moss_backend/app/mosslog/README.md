# MossLog

MossLog 是一个面向 Python Agent 本地开发调试的轻量日志面板。

它的目标不是替代 LangSmith、OpenTelemetry 或生产级监控系统，而是在你本地开发 LangChain、LangGraph、OpenAI SDK 或自研 Agent 时，用最少的代码把关键运行过程实时显示在浏览器里。

核心用法只有两个函数：

```python
from mosslog import mossview, mosslog

mossview()

mosslog("debug", "agent started")
mosslog("llm", "openai response received", input=messages, output=response)
mosslog("tool", "search finished", input=query, output=result)
```

默认情况下，MossLog：

- 只绑定本地地址 `127.0.0.1`
- 不上传任何数据
- 不写数据库
- 不需要账号
- 使用内存保存最近的日志事件
- 通过 FastAPI + SSE 将日志实时推送到浏览器

## 适用场景

MossLog 适合这些情况：

- 正在开发 Agent，但控制台日志不够直观。
- 想看到真实传给 LLM 的输入和模型输出。
- 想观察 LangGraph 节点流转、工具调用、异常信息。
- 不想把 prompt、用户数据或业务数据上传到第三方观测平台。
- 只需要一次本地调试会话，程序退出后日志可以消失。

MossLog 不适合这些情况：

- 生产环境长期审计
- 多用户团队协作平台
- 远程集中式日志系统
- 自动追踪所有 LangChain、LangGraph 或 OpenAI 调用
- 带权限控制的线上监控系统

这些能力可以后续扩展，但当前版本刻意保持简单。

## 当前功能

- `mossview()` 启动本地调试页面。
- `mosslog(tag, message, **fields)` 手动记录事件。
- FastAPI 后端提供网页、快照、SSE 日志流和清空接口。
- 浏览器通过 SSE 实时收到新日志。
- 前端使用 Swagger 风格的纯色展开块。
- 展开日志后优先显示 Chat / IO View。
- Raw Payload 默认折叠，必要时再查看完整 JSON。
- 支持 tag 过滤：`all`、`llm`、`tool`、`node`、`debug`、`error`。
- 支持前端 Pause / Resume / Clear 图标按钮。
- 内存 ring buffer 默认保留最近 1000 条事件。
- 序列化失败时自动降级为 `repr()`。
- 记录异常时会保存异常类型、消息和 traceback。

## 项目结构

```text
.
├── mosslog/
│   ├── __init__.py          # 导出 mossview 和 mosslog
│   ├── api.py               # 公共 API 和后台服务生命周期
│   ├── hub.py               # 内存事件中心、订阅、清空、ring buffer
│   ├── serializer.py        # 安全序列化
│   ├── server.py            # FastAPI app 和接口
│   └── static/
│       └── index.html       # 前端调试页面
├── examples/
│   └── basic_usage.py       # 基础示例
├── tests/                   # 单元测试
├── docs/superpowers/        # 设计说明和实现计划
└── requirements.txt
```

## 安装依赖

当前版本是本地源码形态，还没有发布到 PyPI。建议在项目根目录使用虚拟环境运行。

Windows PowerShell：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

如果已经有 `.venv`：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

macOS / Linux：

```bash
python -m venv .venv
./.venv/bin/python -m pip install -r requirements.txt
```

## 快速开始

在项目根目录创建一个 Python 文件，例如 `demo.py`：

```python
from mosslog import mossview, mosslog


def main():
    mossview()

    mosslog("debug", "agent started")
    mosslog("input", "user question", text="请帮我整理今天的日报")

    messages = [
        {"role": "user", "content": "请帮我整理今天的日报"}
    ]

    response = {
        "role": "assistant",
        "content": "我会先读取今天的记录，然后整理为日报。"
    }

    mosslog(
        "llm",
        "openai response received",
        model="gpt-4o",
        input=messages,
        output=response,
        duration_ms=1200,
    )


if __name__ == "__main__":
    main()
```

运行：

```powershell
.\.venv\Scripts\python.exe demo.py
```

默认会打开：

```text
http://127.0.0.1:8765
```

如果浏览器没有自动打开，可以手动访问这个地址。

## 运行内置示例

```powershell
.\.venv\Scripts\python.exe examples\basic_usage.py
```

不自动打开浏览器：

```powershell
.\.venv\Scripts\python.exe examples\basic_usage.py --no-browser
```

指定端口：

```powershell
.\.venv\Scripts\python.exe examples\basic_usage.py --port 8791
```

指定示例保持运行的秒数：

```powershell
.\.venv\Scripts\python.exe examples\basic_usage.py --seconds 120
```

## API 说明

### `mossview()`

启动本地浏览器调试页面。

```python
from mosslog import mossview

mossview(
    host="127.0.0.1",
    port=8765,
    open_browser=True,
    max_events=1000,
)
```

参数：

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `host` | `"127.0.0.1"` | FastAPI 服务绑定地址 |
| `port` | `8765` | FastAPI 服务端口 |
| `open_browser` | `True` | 是否自动打开浏览器 |
| `max_events` | `1000` | 内存中最多保留多少条事件 |

行为：

- 服务在后台线程启动，不阻塞你的 Agent 主逻辑。
- 如果同一进程里重复调用 `mossview()`，会复用已有 runtime。
- 如果端口被占用，会抛出包含 `host:port` 的清晰错误。
- 默认只监听 `127.0.0.1`，避免暴露到局域网。

### `mosslog()`

记录一条结构化事件。

```python
from mosslog import mosslog

mosslog(tag: str, message: object = None, **fields)
```

最简单用法：

```python
mosslog("debug", "agent started")
```

带结构化字段：

```python
mosslog(
    "llm",
    "openai response received",
    model="gpt-4o",
    input=messages,
    output=response,
    duration_ms=1420,
)
```

异常记录：

```python
try:
    data = parse_model_output(text)
except ValueError as exc:
    mosslog("error", "failed to parse model output", error=exc)
```

行为：

- `tag` 是自由字符串。
- `message` 是主信息，可以是字符串、字典、列表、异常或任意对象。
- `fields` 用来存结构化细节，例如 `input`、`output`、`model`、`usage`、`duration_ms`。
- 如果对象不能 JSON 序列化，会自动转为 `repr()`。
- 如果 `mosslog()` 自身出现异常，会返回 `None`，不会中断你的 Agent。

## 推荐 tag

`tag` 没有强制枚举，但推荐约定如下：

| tag | 用途 |
| --- | --- |
| `debug` | 普通调试信息、状态变化 |
| `input` | 用户输入或外部输入 |
| `llm` | 大模型请求、响应、token 信息 |
| `tool` | 工具调用、工具返回 |
| `node` | LangGraph 节点输入、输出、路由 |
| `error` | 异常、解析失败、请求失败 |

未知 tag 也能显示，前端会用中性样式渲染。

## 事件结构

每次调用 `mosslog()` 会生成类似这样的事件：

```json
{
  "id": 1,
  "time": "2026-05-30T10:30:15.431+08:00",
  "tag": "llm",
  "message": "openai response received",
  "fields": {
    "model": "gpt-4o",
    "duration_ms": 1420,
    "input": [
      {
        "role": "user",
        "content": "整理今天的日志"
      }
    ],
    "output": {
      "role": "assistant",
      "content": "我会先读取记录，然后再为你整理。"
    }
  }
}
```

字段说明：

| 字段 | 说明 |
| --- | --- |
| `id` | 当前进程内递增事件 ID |
| `time` | 本地时区 ISO 时间 |
| `tag` | 事件标签 |
| `message` | 事件主信息 |
| `fields` | 额外结构化字段 |

## 前端界面说明

打开 `mossview()` 后，页面会显示实时事件流。

### 顶部状态

- `connected`：浏览器已连接 SSE。
- `paused`：页面暂停显示新日志。
- `disconnected`：浏览器和后端事件流断开。
- `events`：当前前端保存的事件数量。
- `buffer`：后端 ring buffer 最大容量。

### tag 过滤

前端支持按 tag 过滤：

- `ALL`
- `LLM`
- `TOOL`
- `NODE`
- `DEBUG`
- `ERROR`

过滤只影响浏览器展示，不会删除后端日志。

### Pause / Resume 图标

Pause 图标的作用是暂停页面可见列表更新。

重要说明：

- 不会暂停你的 Agent。
- 不会断开 SSE 连接。
- 新事件仍然会从后端发到浏览器。
- 暂停期间的新事件会暂存在前端的 `pendingEvents`。

点击后按钮会变成 Resume 图标。再点 Resume，会把暂停期间积累的日志一次性追加到页面。

### Clear 图标

Clear 会清空当前调试日志。

它会：

- 请求后端 `POST /clear`
- 清空后端内存 buffer
- 清空当前页面列表
- 向其他已连接页面广播 clear 事件，让它们也同步清空

Clear 不会停止 Agent，也不会关闭服务。

### 展开事件

每条日志都是一个可展开块。

折叠状态下显示：

- 时间
- tag
- message
- 简要 metadata

展开后优先显示 Chat / IO View：

- `input` 会显示为 Input 或 User / Input。
- `output` 会显示为 Output 或 Assistant / Output。
- `tool_call` / `tool_calls` 会显示为 Tool Call。
- `error` 会显示为 Error。

底部的 Raw Payload 默认折叠，需要排查字段时再展开。

## 后端接口

MossLog 的 FastAPI viewer 暴露这些本地接口：

### `GET /`

返回前端页面。

### `GET /snapshot`

返回当前内存中的事件快照。

响应示例：

```json
{
  "events": [],
  "max_events": 1000
}
```

页面刷新时会先请求 `/snapshot`，避免刷新后丢失当前进程内已有日志。

### `GET /events`

SSE 实时事件流。

浏览器使用：

```javascript
new EventSource("/events")
```

事件格式：

```text
event: mosslog
data: {"id": 1, "tag": "llm", "...": "..."}
```

### `POST /clear`

清空内存日志，并向所有 SSE 订阅者广播：

```json
{"type": "clear"}
```

## OpenAI SDK 手动接入示例

MossLog 不会自动 monkey patch OpenAI SDK。推荐手动记录关键位置。

```python
from time import perf_counter

from openai import OpenAI
from mosslog import mossview, mosslog


client = OpenAI()

mossview()

messages = [
    {"role": "user", "content": "请帮我总结这段文本"}
]

mosslog("llm", "openai request", model="gpt-4o", input=messages)

start = perf_counter()
response = client.chat.completions.create(
    model="gpt-4o",
    messages=messages,
)
duration_ms = round((perf_counter() - start) * 1000)

mosslog(
    "llm",
    "openai response received",
    model="gpt-4o",
    output=response,
    duration_ms=duration_ms,
    usage=getattr(response, "usage", None),
)
```

如果 `response` 不能直接 JSON 序列化，MossLog 会自动转成 `repr()`。如果你希望前端展示更清楚，可以自己提取关键字段：

```python
mosslog(
    "llm",
    "openai response received",
    model=response.model,
    output=response.choices[0].message.content,
    usage=response.usage,
)
```

## LangGraph 手动接入示例

可以在每个 node 的入口和出口记录状态。

```python
from mosslog import mosslog


def planner_node(state):
    mosslog("node", "planner input", input=state)

    next_state = {
        **state,
        "next": "search",
    }

    mosslog(
        "node",
        "planner node -> search",
        input=state,
        output=next_state,
        next="search",
    )

    return next_state
```

如果你想记录路由原因：

```python
mosslog(
    "node",
    "route decision",
    input=state,
    output={"next": "tool"},
    reason="model requested a tool call",
)
```

## Tool 调用记录示例

```python
from time import perf_counter
from mosslog import mosslog


def web_search(query: str):
    mosslog("tool", "web_search input", input=query)

    start = perf_counter()
    result = run_search(query)
    duration_ms = round((perf_counter() - start) * 1000)

    mosslog(
        "tool",
        "web_search finished",
        input=query,
        output=result,
        duration_ms=duration_ms,
    )

    return result
```

## 错误记录示例

```python
from mosslog import mosslog


try:
    parsed = parse_json(model_output)
except Exception as exc:
    mosslog(
        "error",
        "failed to parse model output",
        error=exc,
        input=model_output,
    )
    raise
```

异常会被序列化为：

```json
{
  "type": "ValueError",
  "message": "bad output",
  "traceback": ["..."]
}
```

## 隐私和安全

MossLog 当前默认是本地调试工具：

- 默认绑定 `127.0.0.1`。
- 默认不写数据库。
- 默认不上传任何日志。
- 程序退出后，内存日志自然消失。

但你仍然需要注意：

- 你传给 `mosslog()` 的内容会显示在本地浏览器里。
- 如果你把 `host` 改成 `0.0.0.0`，局域网内其他设备可能可以访问。
- 当前版本没有内置 API key、手机号、邮箱等敏感信息脱敏。
- 不建议在生产环境直接暴露该 viewer。

如果需要记录敏感对象，建议调用前手动裁剪：

```python
mosslog(
    "llm",
    "request",
    input="[redacted]",
    model="gpt-4o",
)
```

## 常见问题

### 页面打不开

确认你的脚本里调用了：

```python
mossview()
```

然后访问：

```text
http://127.0.0.1:8765
```

如果你设置了自定义端口，要访问对应端口。

### 端口被占用

如果 `8765` 被占用，可以换端口：

```python
mossview(port=8791)
```

### 调用了 `mosslog()` 但页面没变化

检查：

- 是否先调用了 `mossview()`。
- 页面是否显示 `connected`。
- 当前 tag 过滤是否把事件隐藏了。
- 是否点了 Pause，导致事件暂存在前端。
- 是否在另一个 Python 进程里调用 `mosslog()`。当前版本的内存事件中心只在同一个 Python 进程内生效。

### Pause 后日志丢了吗

没有。

Pause 只暂停前端可见列表更新。暂停期间到达浏览器的事件会存进 `pendingEvents`。点击 Resume 后会追加显示。

### Clear 后还能恢复吗

不能。

Clear 会清空后端内存 buffer。当前版本没有持久化存储。

### 能不能跨进程记录日志

当前版本不支持。

`mosslog()` 写入的是当前 Python 进程内的全局 `EventHub`。如果你有多个进程，各进程的内存不共享。

后续可以扩展为：

- 独立 collector 服务
- HTTP ingest endpoint
- SQLite 持久化
- 多进程队列

### 能不能自动接入 LangChain / LangGraph

当前版本不自动接入。

推荐第一阶段手动调用 `mosslog()`，这样最稳定、最通用，也最不容易记录过多敏感信息。

后续可以扩展：

- LangChain callback handler
- LangGraph node wrapper
- OpenAI SDK helper

## 测试

运行全部测试：

```powershell
.\.venv\Scripts\python.exe -m unittest discover
```

当前测试覆盖：

- 安全序列化
- 异常序列化
- 内存 ring buffer
- SSE subscriber 广播
- 跨线程发布事件
- `mosslog()` 公共 API
- `mossview()` runtime 复用
- 端口占用错误
- FastAPI 路由
- 前端结构约束
- 图标按钮结构

## 开发状态

当前版本已经实现本地调试 MVP：

- 手动日志 API
- 后台 FastAPI viewer
- SSE 实时推送
- 内存日志快照
- 浏览器调试面板

尚未实现：

- 数据库存储
- 日志导出
- 自动脱敏
- 多进程采集
- LangChain callback
- LangGraph 自动包装
- OpenAI SDK 自动包装
- 远程访问认证

## 设计原则

MossLog 的第一版遵循这些原则：

- 手动优先，避免黑盒自动追踪。
- 本地优先，避免默认上传敏感数据。
- 人类可读优先，Raw JSON 作为辅助。
- API 足够简单，随手就能插入调试代码。
- 不影响主程序，日志失败不能拖垮 Agent。

