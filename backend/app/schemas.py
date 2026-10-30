from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class SessionPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    label: str | None = None
    phone: str | None = None
    username: str | None = None
    first_name: str | None = None
    source_bot: str | None = None
    created_at: datetime


class SendCodeRequest(BaseModel):
    phone: str = Field(min_length=5, max_length=32)
    source_bot: str | None = Field(default=None, max_length=64)

    def __repr__(self) -> str:
        return "SendCodeRequest()"


class SendCodeResponse(BaseModel):
    login_id: UUID
    phone: str
    hint: str
    delivery: str
    can_resend: bool = False
    resend_in: int | None = None


class ResendCodeBody(BaseModel):
    login_id: UUID

    def __repr__(self) -> str:
        return "ResendCodeBody()"


class QrStartResponse(BaseModel):
    login_id: UUID
    qr_svg: str
    hint: str


class QrStatusResponse(BaseModel):
    status: Literal["pending", "password", "ready", "error"]
    qr_svg: str | None = None
    hint: str | None = None
    session: SessionPublic | None = None


class QrPasswordBody(BaseModel):
    login_id: UUID
    password: str = Field(min_length=1, max_length=256)

    def __repr__(self) -> str:
        return "QrPasswordBody()"


class SignInRequest(BaseModel):
    login_id: UUID
    code: str = Field(default="", max_length=32)
    password: str | None = Field(default=None, max_length=256)

    def __repr__(self) -> str:
        return "SignInRequest()"


class SignInResponse(BaseModel):
    password_required: bool
    login_id: UUID | None = None
    session: SessionPublic | None = None


class SessionCreate(BaseModel):
    string_session: str = Field(min_length=20, max_length=10000)
    label: str | None = Field(default=None, max_length=120)
    source_bot: str | None = Field(default=None, max_length=64)

    def __repr__(self) -> str:
        return "SessionCreate()"


def normalize_source_bot(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip().lstrip("@")
    if not text:
        return None
    return text[:64]


def session_to_public(row: Any) -> SessionPublic:
    return SessionPublic(
        id=row.id,
        label=row.label,
        phone=row.phone,
        username=row.username,
        first_name=row.first_name,
        source_bot=getattr(row, "source_bot", None),
        created_at=row.created_at,
    )
