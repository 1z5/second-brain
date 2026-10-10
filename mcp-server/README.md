# second-brain MCP Server

把个人知识库暴露成 **MCP（Model Context Protocol）工具**——任何 MCP 客户端（Claude Desktop、DSH、Cursor 等）连上后，就能让 AI 直接检索和问答你的私有知识库。

## 为什么值得看

- **标准库从零实现 MCP stdio 协议**（JSON-RPC 2.0 逐行帧、initialize/tools/list/tools/call/通知语义、isError 与协议层错误的区分）——因为开发机没有 Python 3.10+ 装不了官方 SDK，干脆把协议本身实现了。协议就是契约，与服务器用什么语言写无关。
- 复用 second-brain 核心逻辑（切块/embedding/检索/生成），不重造轮子。

## 工具

| 工具 | 说明 | 依赖 |
|---|---|---|
| `search_knowledge(query, top_k=4)` | 语义检索，返回最相关片段（含来源） | 本地 Ollama embedding |
| `ask_knowledge(question)` | RAG 问答：检索 + step-5 生成，回答带 `[编号]` 引用 | Ollama + step-5 key |
| `add_document(text, title)` | 导入文档（段落切块 + 向量化 + LanceDB 增量入库） | Ollama |
| `list_sources()` | 列出已导入文档 | 无（随时可用） |

## 快速开始

前置：second-brain 已配置（`.env` 有 step-5 key）、本地 Ollama 已启动（`ollama serve`）、知识库已有索引（`python3 ingest.py`）。

```bash
# 直接跑（stdio 传输，供 MCP 客户端接入）
.venv/bin/python mcp-server/server.py

# 跑协议测试（11 项：握手/工具列表/调用/错误路径/未知方法）
.venv/bin/python mcp-server/test_server.py
```

## 客户端配置

**Claude Desktop**（`~/Library/Application Support/Claude/claude_desktop_config.json`）：

```json
{
  "mcpServers": {
    "second-brain": {
      "command": "/Users/zephyr/Downloads/second-brain/.venv/bin/python",
      "args": ["/Users/zephyr/Downloads/second-brain/mcp-server/server.py"]
    }
  }
}
```

**DSH / 其他客户端**：任何能配置 stdio MCP server 的客户端，填同一条 command + args 即可。

## 协议实现要点（面试可能问）

- **消息帧**：stdio 上逐行 JSON（一条消息一行），stdout 只走协议、日志全走 stderr
- **握手**：`initialize` 返回 protocolVersion/capabilities/serverInfo；客户端的 `notifications/initialized` 是通知（无 id），**不应答**
- **错误分两层**：协议层错误（未知方法 → `-32601`）用 JSON-RPC error；工具执行失败（如 Ollama 没启动）用 `isError: true` 的结果——客户端能显示，但不是协议失败
- **踩过的坑**：`ingest/ask` 是脚本式设计，用 `SystemExit` 报「还没有索引」这类正常错误；`SystemExit` 继承 `BaseException`，只捕 `Exception` 会让它杀死 server 进程——工具分发必须捕 `(Exception, SystemExit)`

## 测试

11 项协议断言全部通过：initialize 握手、4 工具注册与 schema、`list_sources` 真实调用、Ollama 未启动时的干净报错、未知方法/未知工具的错误路径、通知不应答。
