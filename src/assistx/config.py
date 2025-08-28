
from dataclasses import dataclass
import os
from dotenv import load_dotenv

load_dotenv()

@dataclass
class Settings:
    neo4j_uri: str = os.getenv("NEO4J_URI", "bolt://localhost:7687")
    neo4j_user: str = os.getenv("NEO4J_USER", "neo4j")
    neo4j_password: str = os.getenv("NEO4J_PASSWORD", "neo4j")

    ollama_host: str = os.getenv("OLLAMA_HOST", "http://localhost:11434")
    ollama_model: str = os.getenv("OLLAMA_MODEL", "llama3.1:8b")

    tavily_api_key: str | None = os.getenv("TAVILY_API_KEY")

    # execution safety
    py_max_seconds: int = int(os.getenv("PY_MAX_SECONDS", "20"))
    py_max_mem_mb: int = int(os.getenv("PY_MAX_MEM_MB", "256"))

    cache_path: str = os.getenv("CACHE_PATH", ".assistx_cache.sqlite")

settings = Settings()
