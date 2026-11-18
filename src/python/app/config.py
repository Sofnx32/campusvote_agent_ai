import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from .errors import AgentError

# El servicio Python se arranca con uvicorn directamente, así que no hereda el
# `--env-file` de Node. Buscamos el .env de la raíz del repositorio subiendo por
# los ancestros del módulo (en desarrollo está en la raíz; en el contenedor de
# Render no existe y las variables llegan por el entorno). Las variables ya
# exportadas por el sistema tienen prioridad (override=False), como en producción.
def _load_repository_env() -> None:
    # Skip loading .env during tests to avoid interfering with test mocks
    if os.getenv("PYTEST_CURRENT_TEST") or os.getenv("TESTING"):
        return
    for parent in Path(__file__).resolve().parents:
        candidate = parent / ".env"
        if candidate.is_file():
            load_dotenv(candidate, override=False)
            return


_load_repository_env()


@dataclass(frozen=True)
class Settings:
    environment: str = os.getenv("NODE_ENV", "development")
    service_token: str = os.getenv("AI_SERVICE_TOKEN", "")
    mtls_required: bool = os.getenv("AI_MTLS_REQUIRED", "false").lower() == "true"
    mtls_ca_path: str = os.getenv("AI_MTLS_CA_PATH", "")
    mtls_cert_path: str = os.getenv("AI_MTLS_CERT_PATH", "")
    mtls_key_path: str = os.getenv("AI_MTLS_KEY_PATH", "")
    context_public_key_path: str = os.getenv("AI_CONTEXT_PUBLIC_KEY_PATH", "")
    context_public_key: str = os.getenv("AI_CONTEXT_PUBLIC_KEY", "")
    context_issuer: str = os.getenv("AI_CONTEXT_ISSUER", "campusvote-api")
    context_audience: str = os.getenv("AI_CONTEXT_AUDIENCE", "campusvote-agent-ai")
    node_tools_url: str = os.getenv("NODE_TOOLS_URL", "http://127.0.0.1:3000")
    llm_provider: str = os.getenv("LLM_PROVIDER", "openai-compatible")
    llm_url: str = os.getenv("LLM_URL", os.getenv("LLM_BASE_URL", ""))
    llm_model: str = os.getenv("LLM_MODEL", "")
    llm_api_key: str = os.getenv("LLM_API_KEY", "")
    llm_fallback_url: str = os.getenv("LLM_FALLBACK_URL", "")
    llm_fallback_model: str = os.getenv("LLM_FALLBACK_MODEL", "")
    llm_fallback_api_key: str = os.getenv("LLM_FALLBACK_API_KEY", "")
    llm_timeout_seconds: float = float(os.getenv("LLM_TIMEOUT_SECONDS", "7"))
    llm_total_budget_seconds: float = float(os.getenv("LLM_TOTAL_BUDGET_SECONDS", "18"))
    # LLM output token limit. `llm_output_token_param` selects the provider-compatible
    # request field: "max_tokens", "max_completion_tokens" or "auto" (heuristic by model).
    llm_max_output_tokens: int = int(os.getenv("LLM_MAX_OUTPUT_TOKENS", "2000"))
    llm_output_token_param: str = os.getenv("LLM_OUTPUT_TOKEN_PARAM", "auto").strip().lower()
    # Fallback budget reserved from total budget (seconds).
    llm_fallback_budget_seconds: float = float(os.getenv("LLM_FALLBACK_BUDGET_SECONDS", "5"))
    # Max LLM retries (total attempts = 1 + this value, hard-capped at 3 total).
    llm_max_retries: int = int(os.getenv("LLM_MAX_RETRIES", "2"))
    # Base delay for exponential backoff (seconds).
    llm_retry_base_delay_seconds: float = float(os.getenv("LLM_RETRY_BASE_DELAY_SECONDS", "1.0"))
    # Maximum backoff delay (seconds).
    llm_retry_max_delay_seconds: float = float(os.getenv("LLM_RETRY_MAX_DELAY_SECONDS", "10.0"))
    # Minimum time reserved to actually perform the next attempt after a backoff.
    llm_retry_min_next_attempt_seconds: float = float(os.getenv("LLM_RETRY_MIN_NEXT_ATTEMPT_SECONDS", "0.5"))
    max_tool_calls: int = int(os.getenv("AI_MAX_TOOL_CALLS", "5"))
    # Coherent with Node `chatTimeoutMs=25s` and AI Context JWT TTL `60s`:
    # 20s (agent) < 25s (Node HTTP) < 60s (JWT). Never raise this above the
    # Node timeout without raising `AI_CHAT_TIMEOUT_MS` in the same deploy.
    agent_deadline_seconds: float = float(os.getenv("AI_AGENT_DEADLINE_SECONDS", "20"))
    tool_timeout_seconds: float = float(os.getenv("AI_TOOL_TIMEOUT_SECONDS", "5"))
    conversation_max_messages: int = int(os.getenv("AI_CONVERSATION_MAX_MESSAGES", "20"))
    conversation_ttl_seconds: int = int(os.getenv("AI_CONVERSATION_TTL_SECONDS", "1800"))

    def public_key(self) -> str:
        if self.context_public_key:
            return self.context_public_key.replace("\\n", "\n")
        if not self.context_public_key_path:
            return ""
        with open(self.context_public_key_path, encoding="utf-8") as file:
            return file.read()


settings = Settings()


class ConfigurationError(ValueError):
    """Configuración de deadlines/presupuestos imposible o contradictoria."""


# Todos los campos temporales que deben ser estrictamente positivos.
TIMING_FIELDS = (
    "agent_deadline_seconds",
    "llm_timeout_seconds",
    "llm_total_budget_seconds",
    "llm_fallback_budget_seconds",
    "llm_retry_base_delay_seconds",
    "llm_retry_max_delay_seconds",
    "llm_retry_min_next_attempt_seconds",
    "tool_timeout_seconds",
)


def deadline_chain(config: Settings | None = None) -> dict[str, float]:
    """Cadena efectiva de deadlines (segundos) usada por el agente Python.

    Frontend -> Node HTTP -> AI Context JWT -> Python deadline
    -> Presupuesto LLM (principal + fallback) -> Tools. Node no es importable
    desde Python, así que sus valores se leen del entorno.
    """
    cfg = config or settings
    context_ttl = float(os.getenv("AI_CONTEXT_TTL_SECONDS", "60"))
    node_http = float(os.getenv("AI_CHAT_TIMEOUT_MS", "25000")) / 1000
    return {
        "ai_context_jwt_ttl": context_ttl,
        "node_http_timeout": node_http,
        "python_agent_deadline": cfg.agent_deadline_seconds,
        "llm_total_budget": cfg.llm_total_budget_seconds,
        "llm_fallback_budget": cfg.llm_fallback_budget_seconds,
        "llm_request_timeout": cfg.llm_timeout_seconds,
        "tool_timeout": cfg.tool_timeout_seconds,
    }


def validate_deadline_chain(config: Settings | None = None) -> list[str]:
    """Devuelve los problemas de la cadena (diagnóstico interno).

    `validate_configuration()` es la única entrada productiva y convierte
    esta lista en un error de arranque; nunca se limita a loguear warnings.
    """
    cfg = config or settings
    chain = deadline_chain(cfg)
    problems: list[str] = []
    for field in TIMING_FIELDS:
        value = getattr(cfg, field)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
            problems.append(f"{field} must be > 0")
    for name in ("ai_context_jwt_ttl", "node_http_timeout"):
        if chain[name] <= 0:
            problems.append(f"{name} must be > 0")
    if not chain["python_agent_deadline"] < chain["node_http_timeout"]:
        problems.append("python_agent_deadline must be lower than node_http_timeout")
    if not chain["node_http_timeout"] < chain["ai_context_jwt_ttl"]:
        problems.append("node_http_timeout must be lower than ai_context_jwt_ttl")
    if not chain["llm_total_budget"] <= chain["python_agent_deadline"]:
        problems.append("llm_total_budget must not exceed python_agent_deadline")
    if not 0 < chain["llm_fallback_budget"] < chain["llm_total_budget"]:
        problems.append("llm_fallback_budget must be > 0 and < llm_total_budget")
    if not chain["llm_request_timeout"] <= chain["llm_total_budget"]:
        problems.append("llm_request_timeout must not exceed llm_total_budget")
    reserve = cfg.llm_retry_min_next_attempt_seconds
    if chain["llm_fallback_budget"] < reserve:
        problems.append("llm_fallback_budget must be >= llm_retry_min_next_attempt_seconds")
    if chain["llm_total_budget"] - chain["llm_fallback_budget"] < reserve:
        problems.append(
            "llm_total_budget minus llm_fallback_budget must be >= "
            "llm_retry_min_next_attempt_seconds"
        )
    if chain["tool_timeout"] > chain["python_agent_deadline"]:
        problems.append("tool_timeout must not exceed python_agent_deadline")
    if chain["llm_total_budget"] + reserve > chain["python_agent_deadline"]:
        problems.append(
            "llm_total_budget plus llm_retry_min_next_attempt_seconds must not "
            "exceed python_agent_deadline"
        )
    return problems


def validate_configuration(config: Settings | None = None) -> None:
    """Fail fast: lanza ConfigurationError si la configuración es imposible."""
    problems = validate_deadline_chain(config)
    if problems:
        raise ConfigurationError(
            "Invalid deadline/budget configuration: " + "; ".join(problems)
        )


def effective_budget_seconds(
    deadline_at: str,
    configured_seconds: float | None = None,
    *,
    now: datetime | None = None,
) -> float:
    """Presupuesto efectivo = min(configurado, tiempo restante de `deadline_at`).

    Falla rápido con AI_DEADLINE_EXCEEDED si el deadline ya venció o es inválido.
    Como el presupuesto LLM es ≤ agent_deadline (validado en arranque), acotar
    aquí el deadline global acota transitivamente todos los presupuestos LLM.
    """
    try:
        deadline = datetime.fromisoformat(str(deadline_at).replace("Z", "+00:00"))
    except (TypeError, ValueError) as error:
        raise AgentError(
            "AI_DEADLINE_EXCEEDED", "Invalid request deadline", 504, True,
            details={"reason": "INVALID_DEADLINE_AT"},
        ) from error
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=timezone.utc)
    remaining = (deadline - (now or datetime.now(timezone.utc))).total_seconds()
    if remaining <= 0:
        raise AgentError(
            "AI_DEADLINE_EXCEEDED", "Request deadline already exceeded", 504, True,
            details={"remaining_seconds": round(remaining, 2)},
        )
    configured = float(
        configured_seconds if configured_seconds is not None else settings.agent_deadline_seconds
    )
    if configured <= 0:
        raise ConfigurationError("configured_seconds must be > 0")
    return min(configured, remaining)


# Validación durante la carga de configuración: cualquier valor imposible
# detiene la importación con un ConfigurationError descriptivo.
validate_configuration()