class AgentError(Exception):
    def __init__(self, code: str, message: str, status: int = 500, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.retryable = retryable


def error_payload(error: AgentError, correlation_id: str | None) -> dict:
    return {
        "success": False,
        "error": {
            "code": error.code,
            "message": error.message,
            "retryable": error.retryable,
        },
        "meta": {"correlation_id": correlation_id},
    }
