
from dataclasses import dataclass
import os
from dotenv import load_dotenv

load_dotenv()

@dataclass
class Settings:
    runtime_profile: str = os.getenv("ASSISTX_RUNTIME_PROFILE", os.getenv("ASSISTX_DEPENDENCY_MODE", "production"))
    dependency_mode: str = os.getenv("ASSISTX_DEPENDENCY_MODE", os.getenv("ASSISTX_RUNTIME_PROFILE", "production"))
    neo4j_uri: str = os.getenv("NEO4J_URI", "bolt://neo4j:7687")
    neo4j_user: str = os.getenv("NEO4J_USER", "neo4j")
    neo4j_password: str = os.getenv("NEO4J_PASSWORD", "")
    # Password is required at runtime; runtime.validate_runtime_configuration
    # enforces presence for production profiles. Never default to a real secret.
    neo4j_database: str | None = os.getenv("NEO4J_DATABASE")

    llm_backend: str = os.getenv("LLM_BACKEND", "openai")
    llm_model: str = os.getenv("LLM_MODEL", os.getenv("OLLAMA_MODEL", "llama3.1:8b"))
    openai_base_url: str = os.getenv("OPENAI_BASE_URL", "http://host.docker.internal:1234/v1")
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "not-needed")
    embed_model: str = os.getenv("EMBED_MODEL", os.getenv("QA_EMBED_MODEL", "nomic-embed-text"))

    ollama_host: str = os.getenv("OLLAMA_HOST", "http://ollama:11434")
    ollama_model: str = os.getenv("OLLAMA_MODEL", "gemma2:2b")

    overlay_mode: str = os.getenv("ASSISTX_OVERLAY_MODE", "router_plus_assign")

    # W-22: single config-gated switch for the execution authority.
    # paperclip -> Paperclip cutover poller owns execution dispatch.
    # direct   -> direct hermes_agent_adapter poller owns execution dispatch.
    # auto     -> prefer paperclip, fall back to direct if unavailable.
    # Default preserved as-is (was implicit paperclip/direct both running).
    execution_backend: str = os.getenv("EXECUTION_BACKEND", "auto").strip().lower()
    auto_router_base_url: str = os.getenv("AUTO_ROUTER_BASE_URL", "")
    auto_assign_base_url: str = os.getenv("AUTO_ASSIGN_BASE_URL", "")
    auto_router_health_path: str = os.getenv("AUTO_ROUTER_HEALTH_PATH", "/health")
    auto_assign_health_path: str = os.getenv("AUTO_ASSIGN_HEALTH_PATH", "/health")

    tavily_api_key: str | None = os.getenv("TAVILY_API_KEY")

    py_max_seconds: int = int(os.getenv("PY_MAX_SECONDS", "20"))
    py_max_mem_mb: int = int(os.getenv("PY_MAX_MEM_MB", "256"))

    cache_path: str = os.getenv("CACHE_PATH", ".assistx_cache.sqlite")

settings = Settings()
