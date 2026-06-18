from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field, HttpUrl


class SpecialistRegistrationRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    url: HttpUrl
    description: str = Field(default='', max_length=2000)
    tags: list[str] = Field(default_factory=list)
    notes: str = Field(default='', max_length=4000)


class SpecialistRegistrationRecord(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    name: str
    url: str
    description: str = ''
    tags: list[str] = Field(default_factory=list)
    notes: str = ''
    agent_card: dict[str, Any]
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
    )
    card_refreshed_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
    )


class SpecialistSummary(BaseModel):
    id: str
    name: str
    url: str
    description: str
    tags: list[str]
    notes: str
    created_at: datetime
    updated_at: datetime
    card_refreshed_at: datetime
    skills: list[dict[str, Any]] = Field(default_factory=list)


class SpecialistRegistrationResponse(BaseModel):
    specialist: SpecialistSummary
    fetched_agent_card: dict[str, Any]


class SpecialistRefreshResponse(BaseModel):
    specialist: SpecialistSummary
    refreshed_agent_card: dict[str, Any]


class RegistrationFormContract(BaseModel):
    required_fields: list[str]
    optional_fields: list[str]
    card_resolution_path: str
    description: str
