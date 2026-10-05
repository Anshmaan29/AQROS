from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


class OutboxStatus(StrEnum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    DELIVERED = "DELIVERED"
    FAILED = "FAILED"
    DEAD_LETTER = "DEAD_LETTER"


@dataclass
class OutboxStatistics:
    pending: int = 0
    processing: int = 0
    delivered: int = 0
    failed: int = 0
    dead_letter: int = 0
    total: int = 0
    retry_sum: int = 0


@dataclass
class OutboxEvent:
    event_id: str
    topic: str
    payload: bytes
    content_type: str = "application/json"
    event_time: datetime | None = None
    knowledge_time: datetime | None = None
    producer: str = ""
    schema_version: str = "1.0"
    correlation_id: str = ""
    causation_id: str | None = None
    headers: dict[str, str] = field(default_factory=dict)

    status: OutboxStatus = OutboxStatus.PENDING
    retry_count: int = 0
    max_retries: int = 5
    last_error: str | None = None
    created_at: datetime | None = None
    next_retry_at: datetime | None = None
    delivered_at: datetime | None = None


class OutboxRepository(ABC):
    @abstractmethod
    async def add(self, event: OutboxEvent) -> OutboxEvent: ...

    @abstractmethod
    async def claim_ready(
        self, batch_size: int, claim_timeout_seconds: int
    ) -> list[OutboxEvent]: ...

    @abstractmethod
    async def mark_delivered(self, event_id: str) -> None: ...

    @abstractmethod
    async def mark_failed(self, event_id: str, error: str) -> None: ...

    @abstractmethod
    async def mark_dead_letter(self, event_id: str, error: str) -> None: ...

    @abstractmethod
    async def statistics(self) -> OutboxStatistics: ...

    @abstractmethod
    async def delete_processed(self, before: datetime) -> int: ...

    @abstractmethod
    async def reprocess_dead_letters(self, max_events: int = 100) -> int: ...
