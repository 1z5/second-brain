"""第二步：向知识库提问，回答带来源标注。

用法：
    python3 ask.py "RAG 检索不准有哪些原因？"   # 单次提问
    python3 ask.py                                # 进入对话模式
"""
import sys

import lancedb

from config import settings, chat_client, embed_one
from lib_retrieval import tokenize

SYSTEM_PROMPT = """你是用户的个人知识库助手。请严格根据下面给出的「资料片段」回答用户的问题。

规则：
1. 只能使用资料中的信息，绝对不要编造资料里没有的内容。
2. 引用资料内容的句子末尾要用 [编号] 标注来源，例如：番茄钟之后应该休息[1]。
3. 如果资料不足以回答问题，直接说"知识库里没有找到相关内容"，并建议用户补充哪类笔记。
4. 回答用中文，简洁直接。"""


def retrieve(question: str):
    """混合检索：向量语义（本地 embedding）+ 自实现 BM25，RRF 融合。

    不用 LanceDB 的 hybrid/FTS——实测其 FTS 索引会导致跨进程读取
    不稳定（详见 lib_retrieval.py 说明），核心链路不建在库的不稳定
    特性上；BM25 与 RRF 均为自实现，可控可测。
    """
    from lib_retrieval import BM25, rrf_fuse, to_rank

    db = lancedb.connect(str(settings.db_dir))
    if settings.table_name not in db.table_names():
        raise SystemExit("还没有索引，请先运行：python3 ingest.py")
    tbl = db.open_table(settings.table_name)

    # 全库行（个人库规模小，全量读出；同时作为 BM25 语料）
    rows = tbl.to_arrow().to_pylist()
    if not rows:
        return []

    keys = [(r["source"], r["chunk_index"]) for r in rows]
    key_to_row = dict(zip(keys, rows))

    # 向量路：放大候选池，给融合留空间
    query_vec = embed_one(question)
    vec_hits = tbl.search(query_vec).limit(max(settings.top_k * 5, len(rows))).to_list()
    vec_order = [(h["source"], h["chunk_index"]) for h in vec_hits if (h["source"], h["chunk_index"]) in key_to_row]
    vec_rank = to_rank(vec_order)

    # 关键词路：BM25 对全库，按行下标排名
    bm25 = BM25([tokenize(r["text"]) for r in rows])
    bm_scores = bm25.scores(question)
    bm_order = sorted(range(len(rows)), key=lambda i: -bm_scores[i])
    bm_rank = to_rank(bm_order)

    # RRF 融合两路排名
    fused = rrf_fuse(vec_rank, bm_rank)
    ranked = sorted(fused.items(), key=lambda x: -x[1])[:settings.top_k]

    out = []
    for key, _score in ranked:
        row = key_to_row.get(key) if isinstance(key, tuple) else rows[key]
        if row is not None:
            out.append(row)
    return out


def build_user_prompt(question: str, hits) -> str:
    blocks = []
    for i, hit in enumerate(hits, 1):
        blocks.append(f"[{i}] 来源：{hit['source']}\n{hit['text']}")
    return "资料片段：\n\n" + "\n\n".join(blocks) + f"\n\n问题：{question}"


def answer(question: str) -> str:
    hits = retrieve(question)
    if not hits:
        return "（索引是空的，请先运行 python3 ingest.py）"

    resp = chat_client().chat.completions.create(
        model=settings.llm_model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": build_user_prompt(question, hits)},
        ],
        max_tokens=settings.max_tokens,
        reasoning_effort=settings.reasoning_effort,
    )
    content = resp.choices[0].message.content or ""
    if not content.strip():
        content = ("（模型只返回了推理过程，没有正文。"
                   "可能是 max_tokens 太小，请在 .env 里调大 LLM_MAX_TOKENS）")

    sources = []
    for i, hit in enumerate(hits, 1):
        snippet = hit["text"].replace("\n", " ")[:60]
        sources.append(f"  [{i}] {hit['source']}  《{hit['title']}》\n      {snippet}……")
    return content + "\n\n参考来源：\n" + "\n".join(sources)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    if args:
        print(answer(" ".join(args)))
        return
    print("个人知识库已就绪，直接输入问题，exit 退出。")
    while True:
        try:
            q = input("\n你：").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if q.lower() in ("exit", "quit", "退出"):
            break
        if not q:
            continue
        print("\n助手：\n" + answer(q))


if __name__ == "__main__":
    main()
