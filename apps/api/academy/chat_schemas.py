"""Bounded group-chat commands; no upstream room, actor or tool inputs."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


def _safe_text(value, maximum_bytes):
    try:
        encoded = value.encode("utf-8")
    except UnicodeError:
        raise ValueError("Text must contain valid UTF-8 characters") from None
    if len(encoded) > maximum_bytes or any(ord(char) < 32 and char not in "\n\r\t" for char in value):
        raise ValueError("Text is too large or contains unsupported control characters")
    return value


class EmptyChatInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ChatBootstrapInput(EmptyChatInput):
    school_id: str = Field(min_length=1, max_length=64)


class ChatGroupInput(ChatBootstrapInput):
    title: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=1000)
    course_id: str | None = Field(default=None, min_length=1, max_length=64)
    visibility: Literal["school", "class", "private"] = "school"
    join_policy: Literal["open", "approval", "invite"] = "open"
    idempotency_key: str = Field(min_length=8, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")

    @model_validator(mode="after")
    def scope(self):
        self.title = _safe_text(self.title.strip(), 480)
        self.description = _safe_text(self.description.strip(), 4000)
        if not self.title:
            raise ValueError("Give the group a title")
        if self.visibility == "class" and not self.course_id:
            raise ValueError("A class group requires a class")
        if self.visibility == "class" and self.join_policy != "open":
            raise ValueError("Class membership is managed by current enrollment")
        if self.visibility == "private" and self.join_policy == "open":
            raise ValueError("Private groups require approval or an invitation")
        return self


class ChatMessageInput(EmptyChatInput):
    text: str = Field(min_length=1, max_length=4000)
    idempotency_key: str = Field(min_length=8, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")
    reply_to: str | None = Field(default=None, min_length=1, max_length=100)

    @model_validator(mode="after")
    def nonempty(self):
        self.text = _safe_text(self.text.strip(), 16384)
        if not self.text:
            raise ValueError("Write a message")
        if any(ord(char) < 32 and char not in "\n\r\t" for char in self.text):
            raise ValueError("Message contains unsupported control characters")
        return self


class ChatMemberInput(EmptyChatInput):
    principal_id: str = Field(min_length=1, max_length=64)
    action: Literal["invite", "approve", "remove", "reject"]


class ChatReviewInput(EmptyChatInput):
    decision: Literal["approve", "reject"]
