from contextvars import ContextVar

request_id_context: ContextVar[str] = ContextVar("campusvote_request_id", default="-")
