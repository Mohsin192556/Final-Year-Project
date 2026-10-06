from typing import Literal

from pydantic import AnyHttpUrl, BaseModel, Field, field_validator


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=4000)

    @field_validator("content")
    @classmethod
    def content_must_not_be_blank(cls, content: str) -> str:
        content = content.strip()
        if not content:
            raise ValueError("Message content cannot be blank.")
        return content


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    history: list[ChatMessage] = Field(default_factory=list, max_length=12)

    @field_validator("question")
    @classmethod
    def question_must_not_be_blank(cls, question: str) -> str:
        question = question.strip()
        if not question:
            raise ValueError("Question cannot be blank.")
        return question


class Source(BaseModel):
    source_id: str
    title: str
    citation: str
    jurisdiction: str
    source_url: AnyHttpUrl | None
    excerpt: str


class ChatResponse(BaseModel):
    answer: str
    sources: list[Source]
    disclaimer: str


class VerificationRequest(BaseModel):
    verified: bool
