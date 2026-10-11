"""第二步：向知识库提问，回答带来源标注。

用法：
    python3 ask.py "RAG 检索不准有哪些原因？"   # 单次提问
    python3 ask.py                                # 进入对话模式
"""
import sys

import lancedb

from config import settings, chat_client, embed_one

SYSTEM_PROMPT = """你是用户的个人知识库助手。请严格根据下面给出的「资料片段」回答用户的问题。

规则：
1. 只能使用资料中的信息，绝对不要编造资料里没有的内容。
2. 引用资料内容的句子末尾要用 [编号] 标注来源，例如：番茄钟之后应该休息[1]。
3. 如果资料不足以回答问题，直接说"知识库里没有找到相关内容"，并建议用户补充哪类笔记。
4. 回答用中文，简洁直接。"""


def retrieve(question: str):
    db = lancedb.connect(str(settings.db_dir))
    if settings.table_name not in db.table_names():
        raise SystemExit("还没有索引，请先运行：python3 ingest.py")
    tbl = db.open_table(settings.table_name)
    query_vec = embed_one(question)
    try:
        # 混合检索：向量语义 + BM25 关键词，RRF（Reciprocal Rank Fusion）
        # 重排融合两路结果。纯向量对专有名词不敏感，混合后鲁棒得多。
        # 注意：表上没有注册 embedding function，查询向量必须显式传入，
        # 否则 hybrid 查询会抛 "No embedding function for vector"。
        return (tbl.search(question, query_type="hybrid")
                   .vector(query_vec)
                   .limit(settings.top_k)
                   .rerank()
                   .to_list())
    except Exception as e:
        # 降级纯向量检索——但必须留痕，避免静默降级骗过评测
        print(f"[warn] 混合检索失败，降级纯向量: {e}")
        return tbl.search(query_vec).limit(settings.top_k).to_list()


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
