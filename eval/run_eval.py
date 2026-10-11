"""检索质量评测：用标注好的问题集跑 Recall@k，输出量化报告。

用法：
    python3 eval/run_eval.py            # 检索指标（Recall@4，秒级）
    python3 eval/eval_answer.py         # 端到端：检索 + LLM 回答 + 关键事实覆盖（较慢，每题一次生成）

指标说明：
    Recall@4    期望文档是否出现在 top-4 检索结果里（检索层的硬指标）
    事实覆盖率  LLM 回答中命中 key_facts 的比例（生成层的参考指标）
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

QUESTIONS = json.loads((Path(__file__).parent / "questions.json").read_text(encoding="utf-8"))


def run_retrieval_eval():
    """检索指标：Recall@k（默认 k=4，即 settings.top_k）。"""
    from ask import retrieve

    hits, rows = 0, []
    t0 = time.time()
    for q in QUESTIONS:
        t1 = time.time()
        results = retrieve(q["question"])
        elapsed = round((time.time() - t1) * 1000)
        sources = [r["source"] for r in results]
        hit = q["expected_source"] in sources
        hits += 1 if hit else 0
        rank = sources.index(q["expected_source"]) + 1 if hit else None
        rows.append({
            "id": q["id"], "question": q["question"], "hit": hit,
            "rank": rank, "sources": sources, "ms": elapsed,
        })
        mark = "✅" if hit else "❌"
        rank_s = f"第{rank}位" if hit else "未命中"
        print(f"  {mark} {q['id']} {q['question'][:24]:<26} {rank_s:<6} {elapsed}ms")

    total = len(QUESTIONS)
    recall = hits / total
    print(f"\n================ 检索评测报告 ================")
    print(f"Recall@4（期望文档命中率）: {hits}/{total} = {recall:.0%}")
    print(f"平均检索耗时: {round((time.time() - t0) / total * 1000)}ms")
    print(f"未命中问题: {[r['id'] for r in rows if not r['hit']] or '无'}")
    return recall


def run_answer_eval(limit: int = 5):
    """端到端指标：LLM 回答对 key_facts 的覆盖率（抽样，慢）。"""
    from ask import answer

    sample = QUESTIONS[:limit]
    total_facts, hit_facts = 0, 0
    for q in sample:
        ans = answer(q["question"])
        for fact in q["key_facts"]:
            total_facts += 1
            if fact in ans:
                hit_facts += 1
        print(f"  {q['id']} 回答 {len(ans)} 字符，关键事实 {sum(1 for f in q['key_facts'] if f in ans)}/{len(q['key_facts'])}")
    coverage = hit_facts / total_facts if total_facts else 0
    print(f"\n事实覆盖率（抽样 {len(sample)} 题）: {hit_facts}/{total_facts} = {coverage:.0%}")
    return coverage


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "retrieval"
    if mode == "answer":
        run_answer_eval()
    else:
        run_retrieval_eval()
