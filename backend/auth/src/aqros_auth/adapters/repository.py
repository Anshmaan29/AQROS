"""Postgres repositories for the auth service.

Auth is the identity store, so its data must survive a restart: the in-memory
adapters are for tests and dry runs only. These repositories own their own
sessions and commit explicitly.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aqros_auth.adapters.orm import ApprovalRequestModel, UserModel
from aqros_auth.domain.approvals import (
    ApprovalEvent,
    ApprovalRequest,
    ApprovalStatus,
)
from aqros_auth.domain.policy import Role
from aqros_auth.domain.ports import ApprovalRepository, UserRecord, UserRepository


def _user_to_record(row: UserModel) -> UserRecord:
    return UserRecord(
        principal_id=row.principal_id,
        username=row.username,
        display_name=row.display_name,
        password_hash=row.password_hash,
        roles=[Role(r) for r in row.roles if r in {x.value for x in Role}],
        is_service=row.is_service,
        is_active=row.is_active,
        created_at=row.created_at,
        last_login_at=row.last_login_at,
    )


class SqlAlchemyUserRepository(UserRepository):
    """Postgres-backed user store."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def get_by_username(self, username: str) -> UserRecord | None:
        async with self._session_factory() as session:
            row = await session.scalar(select(UserModel).where(UserModel.username == username))
            return _user_to_record(row) if row else None

    async def get_by_principal_id(self, principal_id: str) -> UserRecord | None:
        async with self._session_factory() as session:
            row = await session.get(UserModel, principal_id)
            return _user_to_record(row) if row else None

    async def create(self, record: UserRecord) -> UserRecord:
        async with self._session_factory() as session:
            session.add(
                UserModel(
                    principal_id=record.principal_id,
                    username=record.username,
                    display_name=record.display_name,
                    password_hash=record.password_hash,
                    roles=[r.value for r in record.roles],
                    is_service=record.is_service,
                    is_active=record.is_active,
                    created_at=record.created_at or datetime.now().astimezone(),
                )
            )
            await session.commit()
        return record

    async def update(self, record: UserRecord) -> UserRecord:
        async with self._session_factory() as session:
            row = await session.get(UserModel, record.principal_id)
            if row is None:
                raise ValueError(f"unknown principal: {record.principal_id}")
            row.display_name = record.display_name
            row.roles = [r.value for r in record.roles]
            row.is_active = record.is_active
            row.password_hash = record.password_hash
            await session.commit()
        return record

    async def list_all(self) -> list[UserRecord]:
        async with self._session_factory() as session:
            rows = (await session.scalars(select(UserModel).order_by(UserModel.username))).all()
            return [_user_to_record(r) for r in rows]

    async def exists(self, username: str) -> bool:
        async with self._session_factory() as session:
            found = await session.scalar(
                select(UserModel.principal_id).where(UserModel.username == username)
            )
            return found is not None


def _approval_to_request(row: ApprovalRequestModel) -> ApprovalRequest:
    payload: dict[str, Any] = json.loads(row.payload or "{}")
    history: list[dict[str, Any]] = json.loads(row.history or "[]")
    # `expires_at` is a derived property (created_at + ttl), so it cannot be
    # assigned. Restore the *ttl* instead — otherwise a request created with a
    # non-default TTL would come back with the 24h default and expire at the
    # wrong time.
    ttl = row.expires_at - row.created_at
    request = ApprovalRequest(
        request_id=row.request_id,
        action=row.action,
        resource=row.resource,
        payload=payload,
        requester_id=row.requester_id,
        created_at=row.created_at,
        ttl=ttl,
        status=ApprovalStatus(row.status),
        approver_id=row.approver_id,
        decided_at=row.decided_at,
        executed_at=row.executed_at,
    )
    request.events = [
        ApprovalEvent(
            from_status=ApprovalStatus(e["from"]) if e.get("from") else None,
            to_status=ApprovalStatus(e["to"]),
            actor_id=e["actor_id"],
            at=datetime.fromisoformat(e["at"]),
            reason=e.get("reason", ""),
        )
        for e in history
    ]
    return request


class SqlAlchemyApprovalRepository(ApprovalRepository):
    """Postgres-backed four-eyes approval store."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def get(self, request_id: str) -> ApprovalRequest | None:
        async with self._session_factory() as session:
            row = await session.get(ApprovalRequestModel, request_id)
            return _approval_to_request(row) if row else None

    async def save(self, request: ApprovalRequest) -> ApprovalRequest:
        history = [
            {
                "from": e.from_status.value if e.from_status else None,
                "to": e.to_status.value,
                "actor_id": e.actor_id,
                "at": e.at.isoformat(),
                "reason": e.reason,
            }
            for e in request.events
        ]
        async with self._session_factory() as session:
            row = await session.get(ApprovalRequestModel, request.request_id)
            if row is None:
                session.add(
                    ApprovalRequestModel(
                        request_id=request.request_id,
                        action=request.action,
                        resource=request.resource,
                        payload=json.dumps(request.payload, separators=(",", ":"), default=str),
                        requester_id=request.requester_id,
                        approver_id=request.approver_id,
                        status=request.status.value,
                        created_at=request.created_at,
                        expires_at=request.expires_at,
                        decided_at=request.decided_at,
                        executed_at=request.executed_at,
                        history=json.dumps(history, separators=(",", ":")),
                    )
                )
            else:
                row.status = request.status.value
                row.approver_id = request.approver_id
                row.decided_at = request.decided_at
                row.executed_at = request.executed_at
                row.history = json.dumps(history, separators=(",", ":"))
                row.payload = json.dumps(request.payload, separators=(",", ":"), default=str)
            await session.commit()
        return request

    async def list_by_status(self, status: ApprovalStatus) -> list[ApprovalRequest]:
        async with self._session_factory() as session:
            rows = (
                await session.scalars(
                    select(ApprovalRequestModel)
                    .where(ApprovalRequestModel.status == status.value)
                    .order_by(ApprovalRequestModel.created_at)
                )
            ).all()
            return [_approval_to_request(r) for r in rows]

    async def list_all(self) -> list[ApprovalRequest]:
        async with self._session_factory() as session:
            rows = (
                await session.scalars(
                    select(ApprovalRequestModel).order_by(ApprovalRequestModel.created_at)
                )
            ).all()
            return [_approval_to_request(r) for r in rows]
