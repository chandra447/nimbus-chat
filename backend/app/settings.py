from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file='.env',
        env_file_encoding='utf-8',
        case_sensitive=False,
        extra='ignore',
    )

    openai_base_url: str = Field(
        default='https://api.openai.com/v1',
        alias='OPENAI_BASE_URL',
    )
    openai_api_key: str = Field(default='', alias='OPENAI_API_KEY')
    openai_model: str = Field(default='gpt-5.4-mini', alias='OPENAI_MODEL')
    openrouter_api_key: str = Field(default='', alias='OPENROUTER_API_KEY')

    sqlite_path: str = Field(default='./data/nimbus-chat.db', alias='SQLITE_PATH')

    orchestrator_host: str = Field(default='0.0.0.0', alias='ORCHESTRATOR_HOST')
    orchestrator_port: int = Field(default=8000, alias='ORCHESTRATOR_PORT')
    orchestrator_public_url: str = Field(
        default='http://localhost:8000',
        alias='ORCHESTRATOR_PUBLIC_URL',
    )

    specialist_host: str = Field(default='0.0.0.0', alias='SPECIALIST_HOST')
    specialist_port: int = Field(default=8001, alias='SPECIALIST_PORT')
    specialist_type: str = Field(
        default='travel',
        alias='SPECIALIST_TYPE',
        description='Which specialist config to run: travel, nutrition, etc.',
    )
    specialist_public_url: str = Field(
        default='http://localhost:8001',
        alias='SPECIALIST_PUBLIC_URL',
    )
    specialist_internal_url: str = Field(
        default='http://localhost:8001',
        alias='SPECIALIST_INTERNAL_URL',
        description=(
            'Internal URL used by the orchestrator to reach the specialist. '
            'Defaults to SPECIALIST_PUBLIC_URL. Set to a Docker service hostname '
            'when the orchestrator and specialist run inside the same network.'
        ),
    )
    specialist_url_remaps: str = Field(
        default='',
        alias='SPECIALIST_URL_REMAPS',
        description=(
            'Comma-separated list of public_url=internal_url mappings for '
            'normalizing specialist URLs registered via the frontend. '
            'Example: http://localhost:8001=http://travel-specialist:8001,'
            'http://localhost:8002=http://nutrition-specialist:8002'
        ),
    )

    tavily_api_key: str = Field(default='', alias='TAVILY_API_KEY')
    tavily_enabled: bool = Field(default=False, alias='TAVILY_ENABLED')

    # Distributed tracing (HoneyHive). When hh_api_key is set, LangChain calls
    # on both the orchestrator and specialists are traced into a single
    # per-conversation HoneyHive session, with specialist spans linked as
    # children of the orchestrator's dispatch span via W3C context propagation.
    hh_api_key: str = Field(default='', alias='HH_API_KEY')
    hh_project: str = Field(default='', alias='HH_PROJECT')
    hh_enable_tracing: bool = Field(default=True, alias='HH_ENABLE_TRACING')

    specialist_card_refresh_ttl_seconds: int = Field(
        default=300,
        alias='SPECIALIST_CARD_REFRESH_TTL_SECONDS',
        description=(
            'How long to trust a persisted specialist agent card before refreshing it. '
            'Use 0 to refresh on every registry read, or -1 to disable TTL refresh.'
        ),
    )
    cors_origins: str = Field(
        default='*',
        alias='CORS_ORIGINS',
        description=(
            'Comma-separated list of origins allowed for CORS. '
            'Use * to allow all origins (development only).'
        ),
    )
    orchestrator_internal_url: str = Field(
        default='http://localhost:8000',
        alias='ORCHESTRATOR_INTERNAL_URL',
        description=(
            'Internal URL that specialists use to POST push notifications '
            'back to the orchestrator webhook (/a2a/callback). In Docker, '
            'use the orchestrator service hostname.'
        ),
    )

    @computed_field
    @property
    def sqlite_absolute_path(self) -> Path:
        return Path(self.sqlite_path).expanduser().resolve()

    @computed_field
    @property
    def sqlite_parent_dir(self) -> Path:
        return self.sqlite_absolute_path.parent

    @computed_field
    @property
    def sqlite_async_url(self) -> str:
        return f'sqlite+aiosqlite:///{self.sqlite_absolute_path}'

    @computed_field
    @property
    def langgraph_sqlite_conn_string(self) -> str:
        return str(self.sqlite_absolute_path)

    @computed_field
    @property
    def tavily_configured(self) -> bool:
        return self.tavily_enabled and bool(self.tavily_api_key)

    @computed_field
    @property
    def specialist_url_remap_map(self) -> dict[str, str]:
        """Parse SPECIALIST_URL_REMAPS into a {public_url: internal_url} dict."""
        remaps: dict[str, str] = {}
        if not self.specialist_url_remaps:
            return remaps
        for pair in self.specialist_url_remaps.split(','):
            pair = pair.strip()
            if '=' not in pair:
                continue
            public, internal = pair.split('=', 1)
            remaps[public.strip().rstrip('/')] = internal.strip().rstrip('/')
        return remaps

    def ensure_directories(self) -> None:
        self.sqlite_parent_dir.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_directories()
    return settings
