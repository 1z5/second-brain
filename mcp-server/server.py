"""second-brain MCP Server —— 用 Python 标准库从零实现 MCP stdio 协议。

不依赖官方 SDK（环境限制：本机无 3.10+ Python），直接实现
Model Context Protocol 的 stdio 传输与 JSON-RPC 2.0 消息帧：

  initialize                → 协议版本/能力协商
  notifications/initialized → 客户端就绪通知（无需响应）
  tools/list                → 工具清单（name/description/inputSchema）
  tools/call                → 调用工具，返回 {content:[{type:"text",...}], isError?}

任何 MCP 客户端（Claude Desktop / DSH / Cursor）均可连接，与用什么语言
实现服务器无关——协议就是契约。

暴露的 4 个工具（复用 second-brain 核心逻辑，不重造轮子）：
  search_knowledge  语义检索知识库（本地 Ollama embedding）
  ask_knowledge     基于知识库的 RAG 问答（检索 + step-5 生成，带引用）
  add_document      导入文档（段落切块 + 向量化 + LanceDB 入库）
  list_sources      列出已导入文档（无 embedding 依赖，随时可用）

运行：python server.py（stdio 传输，消息为逐行 JSON）
"""
import json
import sys
import traceback
from pathlib import Path

# 复用 second-brain 的核心逻辑（父目录的 config/ingest/ask）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO = {"name": "second-brain", "version": "1.0.0"}

# ---------- 工具定义（MCP tools/list 的返回） ----------

TOOLS = [
    {
        "name": "search_knowledge",
        "description": "语义检索知识库，返回最相关的片段（含来源与标题）。依赖本地 Ollama embedding。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "检索问题或关键词"},
                "top_k": {"type": "integer", "description": "返回片段数（默认 4，最大 10）", "default": 4},
            },
            "required": ["query"],
        },
    },
    {
        "name": "ask_knowledge",
        "description": "基于知识库回答问题（RAG：检索 + step-5 生成，回答带 [编号] 引用来源）。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "question": {"type": "string", "description": "要问的问题"},
            },
            "required": ["question"],
        },
    },
    {
        "name": "add_document",
        "description": "把一篇文档导入知识库（自动按段落切块、向量化、LanceDB 入库）。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "文档全文（Markdown 或纯文本）"},
                "title": {"type": "string", "description": "文档标题（也是来源标识）"},
            },
            "required": ["text", "title"],
        },
    },
    {
        "name": "list_sources",
        "description": "列出知识库中已导入的文档来源（不需要 embedding，随时可用）。",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


# ---------- 工具实现 ----------

def tool_list_sources(_args):
    from ingest import connect_db, table_exists
    from config import settings

    db = connect_db(reset=False)
    if not table_exists(db):
        return "（知识库为空——先用 add_document 导入文档）"
    tbl = db.open_table(settings.table_name)
    sources = sorted({row["source"] for row in tbl.search(None).to_list()})
    if not sources:
        return "（知识库为空——先用 add_document 导入文档）"
    return "已导入的文档：\n" + "\n".join(f"- {s}" for s in sources)


def tool_search_knowledge(args):
    from ask import retrieve
    from config import settings

    settings.top_k = max(1, min(int(args.get("top_k", 4)), 10))
    hits = retrieve(args["query"])
    if not hits:
        return "（没有检索到相关内容）"
    lines = []
    for i, hit in enumerate(hits, 1):
        lines.append(f"[{i}] 《{hit['title']}》（{hit['source']}）\n{hit['text']}")
    return "\n\n".join(lines)


def tool_ask_knowledge(args):
    from ask import answer

    return answer(args["question"])


def tool_add_document(args):
    from ingest import build_rows, connect_db, save_rows, table_exists
    from config import embed_texts, settings

    rows = build_rows([{"source": args["title"], "title": args["title"], "text": args["text"]}])
    if not rows:
        return "文档内容为空，未导入"
    vectors = embed_texts([r["text"] for r in rows])
    for row, vec in zip(rows, vectors):
        row["vector"] = vec

    db = connect_db(reset=False)
    if table_exists(db):
        db.open_table(settings.table_name).add(rows)  # 增量追加
    else:
        save_rows(db, rows)  # 首次建表
    return f"已导入《{args['title']}》：{len(rows)} 个分块入库"


TOOL_IMPL = {
    "list_sources": tool_list_sources,
    "search_knowledge": tool_search_knowledge,
    "ask_knowledge": tool_ask_knowledge,
    "add_document": tool_add_document,
}


# ---------- JSON-RPC 2.0 / MCP 协议处理 ----------

def make_result(msg_id, result):
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def make_error(msg_id, code, message):
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def handle(msg):
    """处理一条 JSON-RPC 消息，返回响应 dict；通知类返回 None。"""
    method = msg.get("method")
    msg_id = msg.get("id")  # 通知（无 id）不响应
    params = msg.get("params") or {}

    # 通知：客户端就绪等，直接吞掉
    if msg_id is None:
        return None

    if method == "initialize":
        return make_result(msg_id, {
            "protocolVersion": params.get("protocolVersion") or PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": SERVER_INFO,
        })

    if method == "ping":
        return make_result(msg_id, {})

    if method == "tools/list":
        return make_result(msg_id, {"tools": TOOLS})

    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        impl = TOOL_IMPL.get(name)
        if not impl:
            return make_error(msg_id, -32602, f"未知工具: {name}")
        try:
            text = impl(args)
            return make_result(msg_id, {
                "content": [{"type": "text", "text": str(text)}],
                "isError": False,
            })
        except (Exception, SystemExit) as e:
            # 注意：必须含 SystemExit——ingest/ask 是脚本式设计，用
            # SystemExit 报「还没有索引」等正常错误；它继承 BaseException，
            # 只捕 Exception 会让它杀死 server 进程（实测踩坑）
            msg = str(e) or e.__class__.__name__
            return make_result(msg_id, {
                "content": [{"type": "text",
                             "text": f"工具执行失败: {msg}\n（检索类工具需要本地 Ollama 运行：ollama serve）"}],
                "isError": True,
            })

    return make_error(msg_id, -32601, f"未知方法: {method}")


def main():
    # MCP stdio：逐行 JSON 帧；所有日志走 stderr（stdout 只走协议）
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            sys.stderr.write(f"[server] 非法 JSON 帧: {line[:80]}\n")
            continue
        try:
            resp = handle(msg)
        except Exception:
            traceback.print_exc(file=sys.stderr)
            resp = make_error(msg.get("id"), -32603, "内部错误")
        if resp is not None:
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
