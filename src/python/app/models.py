from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    conversation_id: str | None = None
    correlation_id: str


class ToolCall(BaseModel):
    name: str
    arguments: dict


class LLMResult(BaseModel):
    content: str | None = None
    tool_call: ToolCall | None = None

