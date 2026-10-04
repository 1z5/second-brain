"""所有配置集中在这里。日常只需要改 .env，不用动代码。"""
import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

ROOT = Path(__file__).parent
load_dotenv(ROOT / ".env")


@dataclass
class Settings:
    # ---- LLM（生成回答）----
    llm_base_url: str = field(default_factory=lambda: os.getenv(
        "LLM_BASE_URL", "https://api.stepfun.com/step_plan/v1"))
    llm_api_key: str = field(default_factory=lambda: os.getenv("LLM_API_KEY", ""))
    llm_model: str = field(default_factory=lambda: os.getenv("LLM_MODEL", "step-5-preview"))
    reasoning_effort: str = field(default_factory=lambda: os.getenv(
        "LLM_REASONING_EFFORT", "low"))
    max_tokens: int = field(default_factory=lambda: int(os.getenv("LLM_MAX_TOKENS", "2000")))

    # ---- Embedding（本地跑，保护隐私）----
    embed_base_url: str = field(default_factory=lambda: os.getenv(
        "EMBED_BASE_URL", "http://localhost:11434/v1"))
    embed_api_key: str = field(default_factory=lambda: os.getenv("EMBED_API_KEY", "ollama"))
    embed_model: str = field(default_factory=lambda: os.getenv(
        "EMBED_MODEL", "nomic-embed-text"))

    # ---- 数据位置 ----
    data_dir: Path = ROOT / "data"
    db_dir: Path = ROOT / "db"
    table_name: str = "notes"

    # ---- 检索参数 ----
    chunk_size: int = field(default_factory=lambda: int(os.getenv("CHUNK_SIZE", "400")))
    chunk_overlap: int = field(default_factory=lambda: int(os.getenv("CHUNK_OVERLAP", "60")))
    top_k: int = field(default_factory=lambda: int(os.getenv("TOP_K", "4")))


settings = Settings()


def chat_client() -> OpenAI:
    """回答生成的客户端。任何 OpenAI 兼容接口都能接。"""
    if not settings.llm_api_key:
        raise SystemExit("缺少 LLM_API_KEY：请编辑 .env 填入你的 API Key")
    return OpenAI(api_key=settings.llm_api_key, base_url=settings.llm_base_url)


def embed_client() -> OpenAI:
    """向量化客户端。默认指向本地 Ollama。"""
    return OpenAI(api_key=settings.embed_api_key, base_url=settings.embed_base_url)


def embed_texts(texts):
    """批量向量化，返回 list[list[float]]。"""
    client = embed_client()
    resp = client.embeddings.create(model=settings.embed_model, input=texts)
    return [item.embedding for item in resp.data]


def embed_one(text):
    return embed_texts([text])[0]
