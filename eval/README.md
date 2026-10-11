# 检索质量评测

用标注问题集量化 RAG 管线的两个核心指标：**检索命中率**（Recall@4）和**回答事实覆盖率**。

## 快速开始

```bash
# 前置：Ollama 已启动、模型已拉取、索引已构建
ollama pull nomic-embed-text
python3 ingest.py --reset

# 检索指标（秒级，20 题）
python3 eval/run_eval.py

# 端到端指标（每题一次 LLM 生成，较慢；默认抽样 5 题）
python3 eval/run_eval.py answer
```

## 当前结果（2026-10-11，演示笔记）

| 指标 | 结果 |
|---|---|
| **Recall@4**（期望文档进入 top-4） | **20/20 = 100%**（且全部命中第 1 位） |
| 平均检索耗时 | 37ms |
| **事实覆盖率**（回答命中 key_facts，抽样 5 题） | **9/9 = 100%** |

## 指标定义

- **Recall@4**：问题的 `expected_source` 是否出现在检索结果 top-4 的 source 列表里。这是检索层的硬指标——它不过关，后面的生成一定瞎编。
- **事实覆盖率**：LLM 回答文本中命中该题 `key_facts` 的数量比例。反映“检索到了但生成没用上”的损耗。

## 如何扩展到真实笔记

1. 把你的 `.md` 笔记放进 `data/`，重建索引（`python3 ingest.py --reset`）
2. 按 `eval/questions.json` 的格式追加问题（每题三要素）：
   ```json
   {"id": "q21", "question": "你的问题", "expected_source": "笔记文件名.md", "key_facts": ["答案必须包含的事实"]}
   ```
3. 重跑两个命令，对比数字

**经验值参考**：100 篇以内的个人笔记，Recall@4 低于 80% 就该先调分块（`CHUNK_SIZE`）而不是换模型；专有名词命中率低再上混合检索（BM25）。

## 文件

- `questions.json` — 20 题标注集（10 题覆盖番茄工作法笔记、10 题覆盖 RAG 排障笔记，含单事实/多事实/跨段落综合三类问法）
- `run_eval.py` — 评测脚本（无第三方依赖，复用 `ask.py` 的 retrieve/answer）
