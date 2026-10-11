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
        result = db.list_tables()
        # 坑：0.27.1 的 list_tables() 返回 ListTablesResult 对象而非列表，
        # 直接 `name in result` 永远为 False（曾导致增量更新丢掉全部
        # 未变文件的分块——debug 了一整轮才定位到）
        tables = result.tables if hasattr(result, "tables") else result
        return settings.table_name in tables
    return settings.table_name in db.table_names()


META_TABLE = "file_meta"


def connect_db(reset: bool):
    settings.db_dir.mkdir(parents=True, exist_ok=True)
    db = lancedb.connect(str(settings.db_dir))
    if reset:
        if table_exists(db):
            db.drop_table(settings.table_name)
        if META_TABLE in db.table_names():
            db.drop_table(META_TABLE)
    return db


def save_rows(db, rows):
    # 不同版本的 lancedb API 略有差别，做个兼容
    try:
        tbl = db.create_table(settings.table_name, data=rows, mode="overwrite")
    except TypeError:
        if table_exists(db):
            db.drop_table(settings.table_name)
        tbl = db.create_table(settings.table_name, data=rows)
    return tbl




def existing_sources(db):
    """已导入文件的指纹 {source: hash}（meta 表）。"""
    if META_TABLE not in db.table_names():
        return {}
    tbl = db.open_table(META_TABLE)
    return {row["source"]: row["hash"] for row in tbl.to_arrow().to_pylist()}


def file_hash(path) -> str:
    """文件内容指纹（MD5）。"""
    import hashlib
    return hashlib.md5(path.read_bytes()).hexdigest()


def save_meta(db, records):
    """重写 meta 表（全量当前状态，量小无所谓）。"""
    if META_TABLE in db.table_names():
        db.drop_table(META_TABLE)
    db.create_table(META_TABLE, data=records)


def read_chunks(db):
    """读出索引里现有全部分块。

    注意：不能用 search(None)——表上建了 FTS 索引后，新打开句柄的
    search(None) 会返回空（LanceDB 0.27 实测坑，原句柄却正常）。
    to_arrow() 是可靠的全表扫描。
    """
    if not table_exists(db):
        return []
    return db.open_table(settings.table_name).to_arrow().to_pylist()


def main():
    reset = "--reset" in sys.argv
    docs = load_documents(settings.data_dir)
    if not docs:
        raise SystemExit(f"data/ 目录里没有找到 .md 或 .txt 文件：{settings.data_dir}")

    db = connect_db(reset)

    # 当前磁盘状态：{source: (path, hash)}
    current = {}
    for path in sorted(settings.data_dir.rglob("*")):
        if path.name.upper().startswith("README"):
            continue
        if path.suffix.lower() in (".md", ".txt") and path.is_file():
            rel = str(path.relative_to(settings.data_dir))
            current[rel] = (path, file_hash(path))

    # 已导入状态（meta 表指纹）
    known = {} if reset else existing_sources(db)

    # 差异计算
    to_add = [s for s, (_, h) in current.items() if known.get(s) != h]
    to_delete = [s for s in known if s not in current]
    unchanged = [s for s in current if s not in to_add and s not in to_delete]

    if not to_add and not to_delete:
        print(f"没有变化：{len(unchanged)} 个文件均未改动（指纹一致）。用 --reset 可强制重建。")
        return

    print(f"增量更新：新增/变更 {len(to_add)} 个，删除 {len(to_delete)} 个，未变 {len(unchanged)} 个")

    # 策略：合并重写——保留未变文件的分块，重新生成变更文件的，overwrite 全量写回。
    # （不用 LanceDB 的 delete 谓词做局部删除：实测 delete + count_rows 的版本
    # 语义在这套流程下不稳定；个人库千级分块的重写是秒级，确定性和可测试性优先。）
    keep_rows = [r for r in read_chunks(db) if r["source"] in set(unchanged)] if not reset else []

    if to_add:
        add_docs = []
        for source in to_add:
            path, _ = current[source]
            text = path.read_text(encoding="utf-8")
            add_docs.append({
                "source": source,
                "title": find_title(text, path.name),
                "text": text,
            })
        rows = build_rows(add_docs)
        if rows:
            vectors = embed_texts([r["text"] for r in rows])
            for row, vec in zip(rows, vectors):
                row["vector"] = vec
            print(f"写入 {len(rows)} 个新分块（保留 {len(keep_rows)} 个未变分块）")
            all_rows = keep_rows + rows
        else:
            print("变更的文件没有生成分块（内容为空？）")
            all_rows = keep_rows
    else:
        all_rows = keep_rows

    if all_rows:
        save_rows(db, all_rows)
        print(f"完成：索引现有 {len(all_rows)} 行。")
    else:
        # 全被删光了：清表
        if table_exists(db):
            db.drop_table(settings.table_name)
        print("完成：索引已清空（没有笔记了）。")

    # 更新指纹表
    save_meta(db, [{"source": s, "hash": h} for s, (_, h) in current.items()])
    print("指纹表已更新。")


if __name__ == "__main__":
    main()
