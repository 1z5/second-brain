"""second-brain Web 问答服务：FastAPI + 单页前端。

把 CLI 能力包成服务（简历目标态里的「知识库问答服务」）：

    POST /api/ask      {"question": "..."} → RAG 回答 + 引用来源
    GET  /api/sources  已导入文档列表
    GET  /             单页聊天界面

运行：
    .venv/bin/uvicorn web.server:app --port 8000
    # 打开 http://localhost:8000
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from ask import answer
from ingest import existing_sources

app = FastAPI(title="second-brain", version="1.0.0")


class AskRequest(BaseModel):
    question: str = Field(min_length=2, max_length=200)


@app.get("/api/sources")
def list_sources():
    """已导入的文档列表。"""
    import lancedb
    from config import settings

    db = lancedb.connect(str(settings.db_dir))
    meta = existing_sources(db)
    return {"sources": sorted(meta.keys())}


@app.post("/api/ask")
def ask(req: AskRequest):
    """RAG 问答：检索 + step-5 生成，回答带引用来源。"""
    import time

    t0 = time.time()
    text = answer(req.question)
    return {"answer": text, "elapsed_ms": int((time.time() - t0) * 1000)}


PAGE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>second-brain · 个人知识库</title>
<style>
  body { font-family: -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif;
         max-width: 760px; margin: 0 auto; padding: 24px 16px 64px; color: #1a1f2b; }
  h1 { font-size: 22px; }
  .sub { color: #6b7280; font-size: 14px; margin-bottom: 24px; }
  .row { display: flex; gap: 8px; }
  input { flex: 1; padding: 10px 12px; border: 1px solid #e5e7eb; border-radius: 8px; font-size: 15px; }
  button { padding: 10px 18px; border: none; border-radius: 8px; background: #2563eb;
           color: #fff; font-size: 15px; cursor: pointer; }
  button:disabled { background: #93b4f5; }
  .card { background: #fff; border: 1px solid #e5e7eb; border-radius: 12px;
          padding: 16px 20px; margin-top: 16px; white-space: pre-wrap; line-height: 1.7; }
  .meta { color: #6b7280; font-size: 13px; margin-top: 8px; }
  .src { font-size: 13px; color: #6b7280; margin-top: 16px; }
</style>
</head>
<body>
<h1>second-brain</h1>
<p class="sub">向你的个人知识库提问——回答严格基于已导入笔记，并标注来源。</p>
<form id="f">
  <div class="row">
    <input id="q" placeholder="问点什么…" maxlength="200" required>
    <button id="b">提问</button>
  </div>
</form>
<div id="out"></div>
<div class="src" id="src"></div>
<script>
  fetch('/api/sources').then(r => r.json()).then(d => {
    document.getElementById('src').textContent =
      '已导入 ' + d.sources.length + ' 篇文档：' + d.sources.join('、');
  });
  document.getElementById('f').onsubmit = async (e) => {
    e.preventDefault();
    const q = document.getElementById('q').value;
    const b = document.getElementById('b'), out = document.getElementById('out');
    b.disabled = true; b.textContent = '生成中…'; out.textContent = '';
    try {
      const r = await fetch('/api/ask', {method: 'POST',
        headers: {'Content-Type': 'application/json'}, body: JSON.stringify({question: q})});
      const d = await r.json();
      out.className = 'card';
      out.textContent = r.ok ? d.answer : ('出错了：' + (d.detail || '未知错误'));
      if (r.ok) document.getElementById('src').textContent =
        '耗时 ' + Math.round(d.elapsed_ms / 1000) + ' 秒';
    } finally {
      b.disabled = false; b.textContent = '提问';
    }
  };
</script>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
def index():
    return PAGE


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
