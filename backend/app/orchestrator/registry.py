from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse, urlunparse
from uuid import uuid4

import aiosqlite
from a2a.client import A2ACardResolver
from a2a.client.card_resolver import parse_agent_card
from a2a.server.request_handlers.response_helpers import agent_card_to_dict
import httpx

from app.orchestrator.models import (
    SpecialistRegistrationRecord,
    SpecialistRegistrationRequest,
    SpecialistSummary,
)
from app.settings import Settings


class SpecialistRegistry:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.db_path = str(settings.sqlite_absolute_path)

    async def initialize(self) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                '''
                CREATE TABLE IF NOT EXISTS specialists (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    url TEXT NOT NULL UNIQUE,
                    description TEXT NOT NULL,
                    tags_json TEXT NOT NULL,
                    notes TEXT NOT NULL,
                    agent_card_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    card_refreshed_at TEXT NOT NULL
                )
                '''
            )
            await self._ensure_card_refreshed_column(db)
            await db.commit()

    async def _ensure_card_refreshed_column(self, db: aiosqlite.Connection) -> None:
        cursor = await db.execute('PRAGMA table_info(specialists)')
        columns = {row[1] for row in await cursor.fetchall()}
        if 'card_refreshed_at' not in columns:
            await db.execute(
                'ALTER TABLE specialists ADD COLUMN card_refreshed_at TEXT'
            )
            await db.execute(
                '''
                UPDATE specialists
                SET card_refreshed_at = COALESCE(updated_at, created_at, ?)
                WHERE card_refreshed_at IS NULL OR card_refreshed_at = ''
                ''',
                (datetime.now(timezone.utc).isoformat(),),
            )

    def _normalize_specialist_url(self, base_url: str) -> str:
        """Rewrite localhost/loopback specialist URLs to internal Docker URLs.

        Uses the SPECIALIST_URL_REMAPS setting to map public URLs (that the
        frontend uses) to internal Docker service URLs (that the orchestrator
        container can reach). Falls back to the legacy single-specialist
        normalization for backwards compatibility.
        """
        url = base_url.rstrip('/')

        # 1. Check explicit remap map first.
        remaps = self.settings.specialist_url_remap_map
        if url in remaps:
            return remaps[url]

        # 2. Legacy: normalize localhost:<specialist_port> to internal URL.
        parsed = urlparse(url)
        host = (parsed.hostname or '').lower()
        port = parsed.port
        localhost_hosts = {'localhost', '127.0.0.1', '::1'}
        if host in localhost_hosts and port == self.settings.specialist_port:
            parsed = parsed._replace(
                scheme=urlparse(self.settings.specialist_internal_url).scheme or parsed.scheme,
                netloc=urlparse(self.settings.specialist_internal_url).netloc,
            )
            return urlunparse(parsed).rstrip('/')

        return url

    async def resolve_agent_card(self, base_url: str) -> dict[str, Any]:
        url = self._normalize_specialist_url(base_url)
        async with httpx.AsyncClient(timeout=20.0) as client:
            resolver = A2ACardResolver(client, url)
            card = await resolver.get_agent_card()
        return agent_card_to_dict(card)

    def normalize_agent_card(self, agent_card: dict[str, Any]) -> dict[str, Any]:
        return agent_card_to_dict(parse_agent_card(agent_card))

    async def register(
        self,
        payload: SpecialistRegistrationRequest,
    ) -> SpecialistRegistrationRecord:
        url = self._normalize_specialist_url(str(payload.url))
        agent_card = await self.resolve_agent_card(url)
        normalized_card = self.normalize_agent_card(agent_card)
        now = datetime.now(timezone.utc)

        existing = await self.get_by_url(url)
        record = SpecialistRegistrationRecord(
            id=existing.id if existing else str(uuid4()),
            name=payload.name,
            url=url,
            description=payload.description or normalized_card.get('description', ''),
            tags=payload.tags,
            notes=payload.notes,
            agent_card=normalized_card,
            created_at=existing.created_at if existing else now,
            updated_at=now,
            card_refreshed_at=now,
        )

        await self._upsert_record(record)
        return record

    async def refresh_all(
        self,
        *,
        ignore_errors: bool = False,
    ) -> list[SpecialistRegistrationRecord]:
        refreshed: list[SpecialistRegistrationRecord] = []
        for record in await self.list_records():
            try:
                refreshed.append(await self.refresh_by_id(record.id))
            except Exception:
                if not ignore_errors:
                    raise
        return refreshed

    async def refresh_stale_records(
        self,
        *,
        ignore_errors: bool = True,
    ) -> list[SpecialistRegistrationRecord]:
        ttl_seconds = self.settings.specialist_card_refresh_ttl_seconds
        if ttl_seconds < 0:
            return await self.list_records()

        refreshed: list[SpecialistRegistrationRecord] = []
        now = datetime.now(timezone.utc)
        for record in await self.list_records():
            age_seconds = (now - record.card_refreshed_at).total_seconds()
            if age_seconds < ttl_seconds:
                refreshed.append(record)
                continue
            try:
                refreshed.append(await self.refresh_by_id(record.id))
            except Exception:
                if not ignore_errors:
                    raise
                refreshed.append(record)
        return refreshed

    async def refresh_by_id(self, specialist_id: str) -> SpecialistRegistrationRecord:
        record = await self.get_by_id(specialist_id)
        if record is None:
            raise ValueError(f'Unknown specialist id: {specialist_id}')
        return await self._refresh_record(record)

    async def refresh_by_url(self, url: str) -> SpecialistRegistrationRecord:
        record = await self.get_by_url(url)
        if record is None:
            raise ValueError(f'Unknown specialist url: {url}')
        return await self._refresh_record(record)

    async def _refresh_record(
        self,
        record: SpecialistRegistrationRecord,
    ) -> SpecialistRegistrationRecord:
        agent_card = await self.resolve_agent_card(record.url)
        normalized_card = self.normalize_agent_card(agent_card)
        now = datetime.now(timezone.utc)
        refreshed = record.model_copy(
            update={
                'description': record.description or normalized_card.get('description', ''),
                'agent_card': normalized_card,
                'updated_at': now,
                'card_refreshed_at': now,
            }
        )
        await self._upsert_record(refreshed)
        return refreshed

    async def _upsert_record(self, record: SpecialistRegistrationRecord) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                '''
                INSERT INTO specialists (
                    id, name, url, description, tags_json, notes,
                    agent_card_json, created_at, updated_at, card_refreshed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(url) DO UPDATE SET
                    name = excluded.name,
                    description = excluded.description,
                    tags_json = excluded.tags_json,
                    notes = excluded.notes,
                    agent_card_json = excluded.agent_card_json,
                    updated_at = excluded.updated_at,
                    card_refreshed_at = excluded.card_refreshed_at
                ''',
                (
                    record.id,
                    record.name,
                    record.url,
                    record.description,
                    json.dumps(record.tags),
                    record.notes,
                    json.dumps(record.agent_card),
                    record.created_at.isoformat(),
                    record.updated_at.isoformat(),
                    record.card_refreshed_at.isoformat(),
                ),
            )
            await db.commit()

    async def list_records(self) -> list[SpecialistRegistrationRecord]:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                '''
                SELECT id, name, url, description, tags_json, notes,
                       agent_card_json, created_at, updated_at, card_refreshed_at
                FROM specialists
                ORDER BY updated_at DESC
                '''
            )
            rows = await cursor.fetchall()

        return [self._row_to_record(row) for row in rows]

    async def get_by_id(self, specialist_id: str) -> SpecialistRegistrationRecord | None:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                '''
                SELECT id, name, url, description, tags_json, notes,
                       agent_card_json, created_at, updated_at, card_refreshed_at
                FROM specialists
                WHERE id = ?
                LIMIT 1
                ''',
                (specialist_id,),
            )
            row = await cursor.fetchone()
        return self._row_to_record(row) if row else None

    async def get_by_url(self, url: str) -> SpecialistRegistrationRecord | None:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                '''
                SELECT id, name, url, description, tags_json, notes,
                       agent_card_json, created_at, updated_at, card_refreshed_at
                FROM specialists
                WHERE url = ?
                LIMIT 1
                ''',
                (url.rstrip('/'),),
            )
            row = await cursor.fetchone()
        return self._row_to_record(row) if row else None

    async def list_summaries(self) -> list[SpecialistSummary]:
        records = await self.refresh_stale_records(ignore_errors=True)
        return [self.to_summary(record) for record in records]

    def to_summary(self, record: SpecialistRegistrationRecord) -> SpecialistSummary:
        return self._record_to_summary(record)

    async def render_prompt_fragment(self) -> str:
        records = await self.refresh_stale_records(ignore_errors=True)
        if not records:
            return (
                'Registered specialist agents: none. '
                'Handle the request yourself unless a specialist is registered later.'
            )

        lines = [
            'Registered specialist agents available for routing:',
        ]
        for index, record in enumerate(records, start=1):
            card = record.agent_card
            skills = card.get('skills', []) or []
            lines.append(f'{index}. {record.name}')
            lines.append(f'   URL: {record.url}')
            lines.append(f'   Agent card refreshed at: {record.card_refreshed_at.isoformat()}')
            if record.description:
                lines.append(f'   Description: {record.description}')
            if record.tags:
                lines.append(f'   Tags: {", ".join(record.tags)}')
            if skills:
                lines.append('   Skills:')
            for skill in skills:
                lines.append(
                    f"   - {skill.get('name') or skill.get('id')}: {skill.get('description', '')}"
                )
                examples = skill.get('examples', []) or []
                if examples:
                    lines.append(f"     Examples: {' | '.join(examples[:4])}")
        return '\n'.join(lines)

    def _record_to_summary(
        self,
        record: SpecialistRegistrationRecord,
    ) -> SpecialistSummary:
        return SpecialistSummary(
            id=record.id,
            name=record.name,
            url=record.url,
            description=record.description,
            tags=record.tags,
            notes=record.notes,
            created_at=record.created_at,
            updated_at=record.updated_at,
            card_refreshed_at=record.card_refreshed_at,
            skills=record.agent_card.get('skills', []),
        )

    def _row_to_record(self, row: tuple[Any, ...]) -> SpecialistRegistrationRecord:
        return SpecialistRegistrationRecord(
            id=row[0],
            name=row[1],
            url=row[2],
            description=row[3],
            tags=json.loads(row[4]),
            notes=row[5],
            agent_card=json.loads(row[6]),
            created_at=datetime.fromisoformat(row[7]),
            updated_at=datetime.fromisoformat(row[8]),
            card_refreshed_at=datetime.fromisoformat(row[9] or row[8] or row[7]),
        )
