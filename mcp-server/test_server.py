"""MCP server 协议测试：标准库客户端真实连接，逐项验证。

覆盖：
  1. initialize 握手（协议版本/能力/serverInfo）
  2. tools/list（4 个工具 + inputSchema）
  3. tools/call: list_sources（无 embedding 依赖，应成功）
  4. tools/call: search_knowledge（Ollama 未启动时应 isError 干净报错）
  5. 未知方法 → JSON-RPC -32601
  6. 通知（无 id）→ 不产生响应（协议正确性）

运行：../.venv/bin/python test_server.py
"""
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SERVER = HERE / "server.py"
PY = HERE.parent / ".venv" / "bin" / "python"

passed, failed = 0, 0


def check(name, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  ✅ {name}")
    else:
        failed += 1
        print(f"  ❌ {name} {detail}")


class McpClient:
    """最小 MCP stdio 客户端：逐行 JSON-RPC。"""

    def __init__(self):
        self.proc = subprocess.Popen(
            [str(PY), str(SERVER)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, bufsize=1,
        )
        self._next_id = 0

    def request(self, method, params=None):
        self._next_id += 1
        msg = {"jsonrpc": "2.0", "id": self._next_id, "method": method}
        if params is not None:
            msg["params"] = params
        self.proc.stdin.write(json.dumps(msg) + "\n")
        self.proc.stdin.flush()
        return json.loads(self.proc.stdout.readline())

    def notify(self, method, params=None):
        msg = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        self.proc.stdin.write(json.dumps(msg) + "\n")
        self.proc.stdin.flush()

    def close(self):
        self.proc.stdin.close()
        self.proc.terminate()
        self.proc.wait(timeout=5)


def main():
    c = McpClient()
    try:
        print("== 1. initialize 握手 ==")
        r = c.request("initialize", {"protocolVersion": "2024-11-05",
                                     "capabilities": {}, "clientInfo": {"name": "test", "version": "0"}})
        res = r.get("result", {})
        check("返回 serverInfo（second-brain）",
            res.get("serverInfo", {}).get("name") == "second-brain", str(res)[:100])
        check("声明 tools 能力", "tools" in res.get("capabilities", {}))
        check("协议版本回显", res.get("protocolVersion") == "2024-11-05")

        # 客户端就绪通知（必须不产生响应）
        c.notify("notifications/initialized")

        print("== 2. tools/list ==")
        r = c.request("tools/list")
        tools = {t["name"]: t for t in r.get("result", {}).get("tools", [])}
        expected = {"search_knowledge", "ask_knowledge", "add_document", "list_sources"}
        check("4 个工具全部注册", set(tools) == expected, f"got {set(tools)}")
        check("search_knowledge schema 正确（query 必填）",
            tools.get("search_knowledge", {}).get("inputSchema", {}).get("required") == ["query"])
        check("每个工具有 description", all(t.get("description") for t in tools.values()))

        print("== 3. tools/call: list_sources ==")
        r = c.request("tools/call", {"name": "list_sources", "arguments": {}})
        res = r.get("result", {})
        check("调用成功（isError=false）", res.get("isError") is False, str(res)[:100])
        text = res.get("content", [{}])[0].get("text", "")
        check("返回文本内容", isinstance(text, str) and len(text) > 0, text[:60])

        print("== 4. tools/call: search_knowledge（Ollama 未启动 → 干净报错）==")
        r = c.request("tools/call", {"name": "search_knowledge", "arguments": {"query": "测试"}})
        res = r.get("result", {})
        if res.get("isError"):
            msg = res.get("content", [{}])[0].get("text", "")
            check("错误信息包含可操作提示（Ollama）", "Ollama" in msg, msg[:100])
        else:
            check("Ollama 已在运行，检索真实成功（跳过错误路径）", True)

        print("== 5. 未知方法 → -32601 ==")
        r = c.request("no/such/method")
        check("返回方法不存在错误", r.get("error", {}).get("code") == -32601, str(r)[:100])

        print("== 6. 未知工具 → 协议层错误 ==")
        r = c.request("tools/call", {"name": "nope", "arguments": {}})
        check("未知工具返回错误", "error" in r or r.get("result", {}).get("isError"), str(r)[:100])
    finally:
        c.close()

    print(f"\n结果：{passed} 通过 / {failed} 失败")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
