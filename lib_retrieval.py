"""混合检索算法：BM25 + RRF 融合（纯算法，无外部依赖）。

为什么不直接用 LanceDB 的 hybrid/FTS：实测其 FTS 索引会导致跨进程
读取不稳定（同一条记录时而读到时而读空；0.27.1 已是最新版，无法
升级）。个人项目不能把核心链路建在不稳定的库特性上——BM25 和 RRF
都是标准算法，自己实现约 60 行，完全可控可测。

中文分词：无分词器场景下用字符二元组（character bigram）——
「番茄工作法」→ {番茄, 茄工, 工作, 作法}，中文检索的成熟方案。
"""
import math
import re
from collections import Counter

RRF_K = 60  # RRF 平滑常数（论文默认值）


def tokenize(text: str):
    """字符二元组分词。"""
    text = re.sub(r"\s+", "", text.lower())
    if len(text) < 2:
        return [text] if text else []
    return [text[i:i + 2] for i in range(len(text) - 1)]


class BM25:
    """标准 BM25（Okapi），k1=1.5, b=0.75。"""

    def __init__(self, docs_tokens, k1=1.5, b=0.75):
        self.k1, self.b = k1, b
        self.n = len(docs_tokens)
        self.doc_len = [len(d) for d in docs_tokens]
        self.avgdl = sum(self.doc_len) / self.n if self.n else 0
        self.tf = [Counter(d) for d in docs_tokens]
        df = Counter()
        for d in docs_tokens:
            for term in set(d):
                df[term] += 1
        self.idf = {
            t: math.log((self.n - n + 0.5) / (n + 0.5) + 1)
            for t, n in df.items()
        }

    def scores(self, query: str):
        """返回与 docs 等长的 BM25 分数列表。"""
        q_terms = tokenize(query)
        out = []
        for i, tf in enumerate(self.tf):
            s = 0.0
            dl = self.doc_len[i] or 1
            for t in q_terms:
                f = tf.get(t, 0)
                if f == 0:
                    continue
                idf = self.idf.get(t, 0.0)
                s += idf * f * (self.k1 + 1) / (
                    f + self.k1 * (1 - self.b + self.b * dl / (self.avgdl or 1))
                )
            out.append(s)
        return out


def rrf_fuse(rank_a: dict, rank_b: dict, k: int = RRF_K) -> dict:
    """RRF 融合两个排名。rank_*: {doc_index: 名次(从1开始)} → {doc_index: 融合分}。"""
    fused = {}
    for rank in (rank_a, rank_b):
        for doc_id, position in rank.items():
            fused[doc_id] = fused.get(doc_id, 0.0) + 1.0 / (k + position)
    return fused


def to_rank(indices_in_order):
    """把有序的索引列表转成 {index: 名次}（从 1 开始）。"""
    return {idx: pos for pos, idx in enumerate(indices_in_order, 1)}
