# second-brain: Personal Knowledge Base RAG

Turn your local Markdown / TXT notes into a queryable knowledge base. Questions are answered **grounded in your notes with `[1][2]` citations** — generation runs on the StepFun step-5 API while all embedding is local, so note content never leaves your machine.

## Architecture

```
  data/*.md ──► ingest.py ──► chunk ──► local embedding ──► LanceDB(db/)
                                                │
  your question ──► ask.py ──► embed the same way ──► top-k retrieval ──► step-5 answer with citations
```

## Install (once)

```bash
# 1. Python deps (if .venv already exists)
.venv/bin/pip install -r requirements.txt

# 2. Local embedding: install Ollama and pull a 274MB model
brew install ollama          # or download from ollama.com
# macOS without brew (path verified in this repo): download ollama-darwin.tgz,
# put ollama + llama-server + libggml-*.so in the SAME directory (newer Ollama
# no longer bundles llama-server — missing it causes HTTP 500), then:
ollama serve &               # start the server
ollama pull nomic-embed-text

# 3. Config (already generated, just review)
vi .env
```

## Usage

```bash
# Import notes (first run, or after editing notes: --reset rebuilds)
.venv/bin/python ingest.py

# Ask
.venv/bin/python ask.py "番茄钟被打断了怎么办？"
.venv/bin/python ask.py          # no argument → interactive chat mode

# Retrieval quality eval (20-question labeled set, Recall@4 + fact coverage)
.venv/bin/python eval/run_eval.py
.venv/bin/python eval/run_eval.py answer   # end-to-end (incl. LLM generation, slower)
```

## Project Layout

```
second-brain/
├── data/            # notes go here (subdirectories supported)
├── db/              # LanceDB index (auto-generated, don't edit)
├── config.py        # all tunable parameters
├── ingest.py        # import: chunk → embed → build index
├── ask.py           # ask: retrieve → generate → cited answer
├── eval/            # retrieval eval (20-question set + run_eval.py)
├── mcp-server/      # MCP server exposing the knowledge base as tools
└── .env             # API key and retrieval parameters
```

## FAQ

**Q: `Connection refused localhost:11434`**
Ollama isn't running. Open the Ollama app on macOS, or run `ollama serve`.

**Q: The answer says "nothing found in the knowledge base" but I imported it**
Most likely the chunks are too large and semantics get averaged out. Try lowering `CHUNK_SIZE` to 300 in `.env`.

**Q: I edited a note — why is the answer still stale?**
The current version skips already-imported files; edited notes need a full rebuild via `python ingest.py --reset` (seconds for a few hundred notes). Incremental indexing is on the roadmap.

**Q: Can I switch to a fully local model (no API)?**
Two lines in `.env`, no code change:

```
LLM_BASE_URL=http://localhost:11434/v1
LLM_API_KEY=ollama
LLM_MODEL=qwen2.5:7b
LLM_REASONING_EFFORT=off
```

**Q: What about DeepSeek / Kimi / Qwen?**
Same two lines in `.env` (any OpenAI-compatible endpoint works):

```
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL=deepseek-chat
```

## Roadmap

1. ~~Hybrid retrieval~~: ✅ shipped — **self-implemented BM25 (character-bigram tokenization for Chinese) + RRF fusion** (lib_retrieval.py, ~60 lines of pure algorithms). LanceDB's built-in hybrid/FTS was deliberately NOT used: its FTS index caused unstable cross-process reads (records intermittently read as empty; 0.27.1 is the latest version), and a core pipeline shouldn't rest on unstable library features. Eval holds at Recall@4 20/20
2. ~~Incremental indexing~~: ✅ shipped — MD5 content fingerprints in a meta table; ingest diffs fingerprints and rebuilds only changed files (add/change/delete/unchanged), preserving unchanged chunks via merge-rewrite (seconds for a thousand-chunk personal library). Along the way, fixed a year-old latent bug: lancedb 0.27.1's `list_tables()` returns an object, not a list, so `in` checks were always False — it had been silently dropping data in incremental updates
3. ~~Retrieval eval~~: ✅ shipped — `eval/` has a 20-question labeled set and harness; current **Recall@4 = 20/20 (100%, 37ms avg)** and **fact coverage 9/9** (see eval/README.md)
4. ~~Web UI~~: ✅ shipped — `web/server.py` (FastAPI): single-page chat UI + `POST /api/ask` (RAG Q&A, measured 4.2s with citations) + `GET /api/sources`. Run: `.venv/bin/uvicorn web.server:app --port 8000`

## MCP Server (shipped)

`mcp-server/` exposes the knowledge base as MCP tools (`search_knowledge` / `ask_knowledge` / `add_document` / `list_sources`), callable from Claude Desktop, DSH, and other MCP clients. The protocol layer is implemented from scratch with the standard library (JSON-RPC 2.0 over stdio); 11 protocol tests pass. See [mcp-server/README.md](mcp-server/README.md).

---

[中文文档](README.md)
