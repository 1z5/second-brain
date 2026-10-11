"""第一步：把 data/ 目录里的 Markdown/TXT 笔记切块、向量化、存进 LanceDB。

用法：
    python3 ingest.py            # 增量追加（已导入的文件会跳过）
    python3 ingest.py --reset    # 清空索引，从零重建
"""
import re
import sys
from pathlib import Path

import lancedb

from config import settings, embed_texts, chat_client  # noqa: F401  (chat_client 预热)


# ---------- 文本切块 ----------

def split_paragraphs(text: str):
    """按空行拆段，段是切块的最小单位（不把一句话从中间切断）。"""
    return [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]


def chunk_paragraphs(paragraphs, size: int, overlap: int):
    """把段落贪心合并到约 size 字符；相邻块之间保留 overlap 字符的上下文重叠。"""
    chunks, current, current_len = [], [], 0
    for para in paragraphs:
        if current and current_len + len(para) > size:
            chunks.append("\n\n".join(current))
            tail, tail_len = [], 0
            for prev in reversed(current):          # 取当前块末尾做下一块的开头
                if tail_len >= overlap:
                    break
                tail.insert(0, prev)
                tail_len += len(prev)
            current, current_len = tail, tail_len
        current.append(para)
        current_len += len(para) + 2
    if current:
        chunks.append("\n\n".join(current))
    return chunks


def find_title(text: str, fallback: str) -> str:
    """取第一个 Markdown 标题作为文档标题，没有就用文件名。"""
    for line in text.splitlines():
        if line.startswith("#"):
            return line.lstrip("#").strip()
    return fallback


# ---------- 导入 ----------

def load_documents(data_dir: Path):
    """读取目录下所有 .md / .txt，返回 [{source, title, text}]"""
    docs = []
    for path in sorted(data_dir.rglob("*")):
        # README 是说明文件，不导入
        if path.name.upper().startswith("README"):
            continue
        if path.suffix.lower() in (".md", ".txt") and path.is_file():
            docs.append({
                "source": str(path.relative_to(data_dir)),
                "title": find_title(path.read_text(encoding="utf-8"), path.name),
                "text": path.read_text(encoding="utf-8"),
            })
    return docs


def build_rows(docs):
    """文档 -> 数据库行。每行一个分块。"""
    rows, already = [], set()
    for doc in docs:
        for i, chunk in enumerate(chunk_paragraphs(
                split_paragraphs(doc["text"]),
                settings.chunk_size, settings.chunk_overlap)):
            key = (doc["source"], i)
            if key in already:
                continue
            already.add(key)
            rows.append({
                "text": chunk,
                "source": doc["source"],
                "title": doc["title"],
                "chunk_index": i,
            })
    return rows


def table_exists(db) -> bool:
    """兼容不同版本 lancedb 的表名查询 API。"""
    if hasattr(db, "list_tables"):
        return settings.table_name in db.list_tables()
    return settings.table_name in db.table_names()


def connect_db(reset: bool):
    settings.db_dir.mkdir(parents=True, exist_ok=True)
    db = lancedb.connect(str(settings.db_dir))
    if reset and table_exists(db):
        db.drop_table(settings.table_name)
    return db


def save_rows(db, rows):
    # 不同版本的 lancedb API 略有差别，做个兼容
    try:
        tbl = db.create_table(settings.table_name, data=rows, mode="overwrite")
    except TypeError:
        if table_exists(db):
            db.drop_table(settings.table_name)
        tbl = db.create_table(settings.table_name, data=rows)
    ensure_fts_index(tbl)
    return tbl


def ensure_fts_index(tbl):
    """创建全文索引（BM25 半边）。混合检索 = 向量语义 + BM25 关键词，
    RRF 融合重排——治纯向量对专有名词不敏感的问题。"""
    try:
        tbl.create_fts_index("text", replace=True)
    except Exception as e:
        # FTS 索引失败不阻塞主流程（降级为纯向量检索）
        print(f"[warn] FTS 索引创建失败，将使用纯向量检索: {e}")


def existing_sources(db):
    """已导入的文件列表，用于跳过重复导入。"""
    if not table_exists(db):
        return set()
    tbl = db.open_table(settings.table_name)
    return {row["source"] for row in tbl.search(None).to_list()}


def main():
    reset = "--reset" in sys.argv
    docs = load_documents(settings.data_dir)
    if not docs:
        raise SystemExit(f"data/ 目录里没有找到 .md 或 .txt 文件：{settings.data_dir}")

    db = connect_db(reset)
    done = set() if reset else existing_sources(db)
    new_docs = [d for d in docs if d["source"] not in done]
    if not new_docs:
        print("所有文件都已导入，没有新增内容。用 --reset 可强制重建。")
        return

    print(f"发现 {len(new_docs)} 个新文件，开始切块与向量化……")
    rows = build_rows(new_docs)
    if not rows:
        raise SystemExit("没有生成任何分块，请检查笔记内容。")

    vectors = embed_texts([r["text"] for r in rows])
    for row, vec in zip(rows, vectors):
        row["vector"] = vec

    tbl = save_rows(db, rows)
    print(f"完成：新增 {len(rows)} 个分块，索引现有 {tbl.count_rows()} 行。")


if __name__ == "__main__":
    main()
