from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID


@dataclass(slots=True)
class AmbiguityOption:
    event_id: UUID
    title: str
    subtitle: str


@dataclass(slots=True)
class AmbiguityRequest:
    action: str
    command_payload: dict[str, object]
    options: list[AmbiguityOption]


@dataclass(slots=True)
class ConfirmationRequest:
    action: str
    command_payload: dict[str, object]
    event_id: UUID | None
    summary: str


@dataclass(slots=True)
class QuickAction:
    label: str
    action: str
    payload: dict[str, object] = field(default_factory=dict)


@dataclass(slots=True)
class AssistantResponse:
    text: str
    ambiguity: AmbiguityRequest | None = None
    confirmation: ConfirmationRequest | None = None
    quick_actions: list[QuickAction] = field(default_factory=list)
    metadata: dict[str, object] = field(default_factory=dict)
