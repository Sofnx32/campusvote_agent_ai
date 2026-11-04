import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    environment: str = os.getenv("NODE_ENV", "development")
    service_token: str = os.getenv("AI_SERVICE_TOKEN", "")
    mtls_required: bool = os.getenv("AI_MTLS_REQUIRED", "false").lower() == "true"
    mtls_ca_path: str = os.getenv("AI_MTLS_CA_PATH", "")
    mtls_cert_path: str = os.getenv("AI_MTLS_CERT_PATH", "")
    mtls_key_path: str = os.getenv("AI_MTLS_KEY_PATH", "")
    context_public_key_path: str = os.getenv("AI_CONTEXT_PUBLIC_KEY_PATH", "")
    context_issuer: str = os.getenv("AI_CONTEXT_ISSUER", "campusvote-api")
    context_audience: str = os.getenv("AI_CONTEXT_AUDIENCE", "campusvote-agent-ai")
    node_tools_url: str = os.getenv("NODE_TOOLS_URL", "http://127.0.0.1:3000")
    llm_provider: str = os.getenv("LLM_PROVIDER", "openai-compatible")
    llm_url: str = os.getenv("LLM_URL", "")
    llm_model: str = os.getenv("LLM_MODEL", "")
    llm_api_key: str = os.getenv("LLM_API_KEY", "")
    llm_timeout_seconds: float = float(os.getenv("LLM_TIMEOUT_SECONDS", "7"))
    max_tool_calls: int = int(os.getenv("AI_MAX_TOOL_CALLS", "5"))
    tool_timeout_seconds: float = float(os.getenv("AI_TOOL_TIMEOUT_SECONDS", "5"))
    conversation_max_messages: int = int(os.getenv("AI_CONVERSATION_MAX_MESSAGES", "20"))
    conversation_ttl_seconds: int = int(os.getenv("AI_CONVERSATION_TTL_SECONDS", "1800"))

    def public_key(self) -> str:
        if not self.context_public_key_path:
            return ""
        with open(self.context_public_key_path, encoding="utf-8") as file:
            return file.read()


settings = Settings()
