"""Bounded teacher metadata. Profiles are never identity or permission grants."""
import base64
import binascii
import hashlib
import re
import struct
import zlib
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


def normalized_picture(value):
    """Validate actual PNG scanlines and strip all optional metadata chunks.

    The small RGB/RGBA, noninterlaced subset keeps image parsing bounded without
    an image-codec dependency. No SVG, animation, URL fetching or embedded text
    survives normalization.
    """
    try:
        raw = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error):
        raise ValueError("Picture must contain valid base64 PNG bytes") from None
    if len(raw) > 65536 or not raw.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("Picture must be a PNG of at most 64 KiB")
    offset, header, compressed, ended, idat_ended = 8, None, bytearray(), False, False
    while offset < len(raw):
        if offset + 12 > len(raw):
            raise ValueError("Truncated PNG")
        length = struct.unpack(">I", raw[offset:offset + 4])[0]
        kind = raw[offset + 4:offset + 8]
        end = offset + 12 + length
        if end > len(raw) or not re.fullmatch(b"[A-Za-z]{4}", kind):
            raise ValueError("Invalid PNG chunk")
        content = raw[offset + 8:offset + 8 + length]
        checksum = struct.unpack(">I", raw[offset + 8 + length:end])[0]
        if zlib.crc32(kind + content) & 0xffffffff != checksum:
            raise ValueError("Invalid PNG checksum")
        if header is None and kind != b"IHDR":
            raise ValueError("PNG header required first")
        if kind == b"IHDR":
            if header is not None or length != 13:
                raise ValueError("Invalid PNG header")
            header = content
            width, height, depth, color, compression, filtering, interlace = struct.unpack(">IIBBBBB", header)
            if not (1 <= width <= 512 and 1 <= height <= 512) or depth != 8 or color not in {2, 6} or compression or filtering or interlace:
                raise ValueError("Picture must be a noninterlaced 8-bit RGB/RGBA PNG up to 512 by 512 pixels")
        elif kind == b"IDAT":
            if idat_ended:
                raise ValueError("PNG image chunks must be consecutive")
            compressed.extend(content)
        elif kind == b"IEND":
            if length or not compressed or end != len(raw):
                raise ValueError("Invalid PNG end")
            ended = True
        else:
            if kind in {b"acTL", b"fcTL", b"fdAT"} or not kind[0] & 32:
                raise ValueError("Animated or unsupported PNG")
            if compressed:
                idat_ended = True
        offset = end
    if not ended or header is None:
        raise ValueError("Incomplete PNG")
    stride = width * (4 if color == 6 else 3) + 1
    expected = stride * height
    try:
        decoder = zlib.decompressobj()
        pixels = decoder.decompress(bytes(compressed), expected + 1)
    except zlib.error:
        raise ValueError("Invalid PNG image data") from None
    if len(pixels) != expected or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail or any(pixels[row * stride] > 4 for row in range(height)):
        raise ValueError("Invalid or excessive PNG scanlines")

    def chunk(kind, content):
        return struct.pack(">I", len(content)) + kind + content + struct.pack(">I", zlib.crc32(kind + content) & 0xffffffff)

    clean = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(pixels, 9)) + chunk(b"IEND", b"")
    if len(clean) > 65536:
        raise ValueError("Normalized PNG exceeds 64 KiB")
    return {"media_type": "image/png", "data_base64": base64.b64encode(clean).decode("ascii"), "sha256": hashlib.sha256(clean).hexdigest(), "width": width, "height": height}


class TeacherPicture(Strict):
    media_type: Literal["image/png"] = "image/png"
    data_base64: str = Field(min_length=1, max_length=87384)

    @field_validator("data_base64")
    @classmethod
    def valid_png(cls, value):
        return normalized_picture(value)["data_base64"]


class AIBehavior(Strict):
    tone: Literal["warm", "neutral", "formal"] = "warm"
    teaching_style: Literal["socratic", "guided_practice", "direct", "adaptive"] = "socratic"
    detail: Literal["concise", "balanced", "detailed"] = "balanced"
    question_frequency: Literal["low", "balanced", "high"] = "balanced"
    encouragement: Literal["restrained", "balanced", "frequent"] = "balanced"
    instructions: str = Field(default="", max_length=4000)
    boundaries: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("instructions")
    @classmethod
    def bounded_instructions(cls, value):
        return value.strip()

    @field_validator("boundaries")
    @classmethod
    def bounded_boundaries(cls, value):
        if any(not item.strip() or len(item) > 500 for item in value):
            raise ValueError("Boundaries require 1 to 500 characters each")
        return [item.strip() for item in value]


class TeacherTutorModel(Strict):
    connection_id: str = Field(min_length=1, max_length=64)
    model: str = Field(min_length=1, max_length=150, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._:/-]{0,149}$")
    max_turns: int = Field(default=8, ge=1, le=30, strict=True)
    max_input_chars: int = Field(default=3000, ge=100, le=10000, strict=True)
    max_output_tokens: int = Field(default=80, ge=30, le=200, strict=True)
    max_total_tokens: int = Field(default=20000, ge=500, le=1000000, strict=True)
    allow_lesson_curation: bool = Field(default=False, strict=True)
    allow_assignment_images: bool = Field(default=False, strict=True)
    curation_max_output_tokens: int = Field(default=700, ge=200, le=1200, strict=True)

    @model_validator(mode="after")
    def image_policy(self):
        if self.allow_assignment_images and not self.allow_lesson_curation:
            raise ValueError("Assignment images require approved lesson curation")
        return self


class TeacherMetadata(Strict):
    name: str = Field(min_length=1, max_length=200)
    biography: str = Field(default="", max_length=10000)
    pronouns: str = Field(default="", max_length=80)
    title: str = Field(default="", max_length=120)
    contact_email: str | None = Field(default=None, max_length=254)
    expertise: list[str] = Field(default_factory=list, max_length=30)
    picture: TeacherPicture | None = None
    ai_behavior: AIBehavior | None = None
    tutor_model: TeacherTutorModel | None = None

    @field_validator("name")
    @classmethod
    def named(cls, value):
        if not value.strip():
            raise ValueError("Teacher name is required")
        return value.strip()

    @field_validator("expertise")
    @classmethod
    def bounded_expertise(cls, value):
        if any(not item.strip() or len(item) > 120 for item in value):
            raise ValueError("Expertise requires 1 to 120 characters per item")
        return list(dict.fromkeys(item.strip() for item in value))

    @field_validator("contact_email")
    @classmethod
    def contact(cls, value):
        if value is not None and not re.fullmatch(r"[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+", value):
            raise ValueError("A contact email address is required")
        return value


class TeacherCreate(TeacherMetadata):
    kind: Literal["human", "ai"]
    principal_id: str | None = Field(default=None, min_length=1, max_length=64)
    agent_placement_id: str | None = Field(default=None, min_length=1, max_length=64)
    status: Literal["pending", "draft", "ready"] | None = None

    @model_validator(mode="after")
    def kind_fields(self):
        if self.kind == "human" and (self.agent_placement_id or self.ai_behavior is not None or self.tutor_model is not None):
            raise ValueError("Human profiles cannot contain agent bindings or AI behavior")
        if self.kind == "ai" and self.principal_id:
            raise ValueError("AI profiles use exact existing agent placements")
        return self


class TeacherUpdate(Strict):
    expected_version: int = Field(ge=1, strict=True)
    name: str | None = Field(default=None, min_length=1, max_length=200)
    biography: str | None = Field(default=None, max_length=10000)
    pronouns: str | None = Field(default=None, max_length=80)
    title: str | None = Field(default=None, max_length=120)
    contact_email: str | None = Field(default=None, max_length=254)
    expertise: list[str] | None = Field(default=None, max_length=30)
    picture: TeacherPicture | None = None
    ai_behavior: AIBehavior | None = None
    tutor_model: TeacherTutorModel | None = None
    status: Literal["pending", "draft", "ready", "archived"] | None = None


class TeacherVersion(Strict):
    expected_version: int = Field(ge=1, strict=True)


class TeacherBinding(TeacherVersion):
    principal_id: str | None = Field(default=None, min_length=1, max_length=64)
    agent_placement_id: str | None = Field(default=None, min_length=1, max_length=64)

    @model_validator(mode="after")
    def exact_one(self):
        if bool(self.principal_id) == bool(self.agent_placement_id):
            raise ValueError("Specify exactly one existing principal or agent placement")
        return self


class TeacherCourses(TeacherVersion):
    course_ids: list[str] = Field(max_length=100)

    @field_validator("course_ids")
    @classmethod
    def exact_unique_courses(cls, value):
        if len(set(value)) != len(value) or any(not item or len(item) > 64 for item in value):
            raise ValueError("Provide unique exact class IDs")
        return value
