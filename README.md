# second-brain：个人知识库 RAG 问答

> [English](README.en.md)

把散落在本地的 Markdown / TXT 笔记导入一个可对话的知识库，提问时**按资料回答并标注来源**。生成用 StepFun step-5 API，向量化在本地跑，笔记内容不出本机。

## 架构

```
  data/*.md ──► ingest.py ──► 切块 ──► 本地 embedding ──► LanceDB(db/)
                                                  │
  你的问题 ──► ask.py ──► 同样向量化 ──► 检索 top_k 片段 ──► step-5 生成带 [1][2] 引用的回答
```

## 安装（只需一次）

```bash
# 1. Python 依赖（已创建好 .venv 的话）
.venv/bin/pip install -r requirements.txt

# 2. 本地 embedding：装 Ollama，拉一个 274MB 的小模型
brew install ollama          # 或到 ollama.com 下载
# macOS 无 brew 时（本仓库实测路径）：下载 ollama-darwin.tgz，解压后把
# ollama、llama-server、libggml-*.so 放同一目录（新版 Ollama 的
# llama-server 不随主程序分发，缺少会报 500），然后：
ollama serve &               # 启动服务
ollama pull nomic-embed-text

# 3. 配置（已生成，检查一下即可）
vi .env
```

## 使用

```bash
# 导入笔记（第一次，或改了笔记想重建时：--reset）
.venv/bin/python ingest.py

# 提问
.venv/bin/python ask.py "番茄钟被打断了怎么办？"
.venv/bin/python ask.py          # 不加参数进入连续对话模式

# 检索质量评测（20 题标注集，Recall@4 + 事实覆盖率）
.venv/bin/python eval/run_eval.py
.venv/bin/python eval/run_eval.py answer   # 端到端（含 LLM 生成，较慢）
```

## 目录结构

```
second-brain/
├── data/            # 笔记放这里，支持子目录
├── db/              # LanceDB 索引（自动生成，别手改）
├── config.py        # 所有可调参数
├── ingest.py        # 导入：切块 → 向量化 → 建索引
├── ask.py           # 提问：检索 → 生成 → 带引用回答
├── eval/            # 检索评测（20 题标注集 + run_eval.py）
├── mcp-server/      # MCP server：知识库暴露成工具
└── .env             # API key 和检索参数
```

## 常见问题

**Q: `Connection refused localhost:11434`**
Ollama 没启动。Mac 上打开 Ollama 应用，或终端跑 `ollama serve`。

**Q: 回答"知识库里没有找到相关内容"，但我明明导入过**
多半是 chunk 太大语义被平均化。在 `.env` 里把 `CHUNK_SIZE` 降到 300 试试。

**Q: 改了以前的笔记，为什么回答还是旧内容？**
改了笔记直接重跑 `python ingest.py` 即可——按文件指纹增量更新（只重建变更的文件），几百篇以内是秒级。

**Q: 想完全切到本地模型，不用 API？**
`.env` 里改两行即可，代码不用动：

```
LLM_BASE_URL=http://localhost:11434/v1
LLM_API_KEY=ollama
LLM_MODEL=qwen2.5:7b
LLM_REASONING_EFFORT=off
```

**Q: 想换成 DeepSeek / Kimi / 通义？**
同样只改 `.env` 的两行（任何 OpenAI 兼容接口都行）：

```
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL=deepseek-chat
```

## 下阶段迭代方向

1. ~~混合检索~~：✅ 已落地——**自实现 BM25（字符二元组中文分词）+ RRF 融合**（lib_retrieval.py，约 60 行纯算法）。不采用 LanceDB 自带 hybrid/FTS：实测其 FTS 索引导致跨进程读取不稳定（同一条记录时而读到时而读空，0.27.1 已是最新版），核心链路不建在库的不稳定特性上。评测保持 Recall@4 20/20
2. ~~增量索引~~：✅ 已落地——文件内容 MD5 指纹存 meta 表，ingest 按指纹差异只重建变更文件（新增/变更/删除/未变四态），未变文件的分块原样保留（合并重写策略，个人库千级分块秒级）。过程中修复一个潜伏一年的 bug：lancedb 0.27.1 的 list_tables() 返回对象而非列表，in 判断永远 False，曾导致增量丢数据
3. ~~检索评测~~：✅ 已落地——`eval/` 有 20 题标注集 + 评测脚本，当前 Recall@4 = 20/20（100%，平均 37ms）、回答事实覆盖率 9/9（详见 eval/README.md）
4. **Web 界面**：替换命令行，可以做成浏览器里随时打开的形式

## MCP Server（已上线）

`mcp-server/` 把知识库暴露成 MCP 工具（`search_knowledge` / `ask_knowledge` / `add_document` / `list_sources`），Claude Desktop、DSH 等客户端可直接调用。协议层用标准库从零实现（JSON-RPC 2.0 + stdio），11 项协议测试通过。详见 [mcp-server/README.md](mcp-server/README.md)。
