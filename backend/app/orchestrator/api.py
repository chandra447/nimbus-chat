from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from app.orchestrator.models import (
    RegistrationFormContract,
    SpecialistRegistrationRequest,
    SpecialistRegistrationResponse,
    SpecialistRefreshResponse,
    SpecialistSummary,
)
from app.orchestrator.registry import SpecialistRegistry

router = APIRouter(prefix='/api/orchestrator', tags=['orchestrator'])


def get_registry(request: Request) -> SpecialistRegistry:
    return request.app.state.specialist_registry


@router.get('/registration-form', response_model=RegistrationFormContract)
async def registration_form_contract() -> RegistrationFormContract:
    return RegistrationFormContract(
        required_fields=['name', 'url'],
        optional_fields=['description', 'tags', 'notes'],
        card_resolution_path='/.well-known/agent-card.json',
        description=(
            'Submit the specialist base URL and the orchestrator will resolve the A2A '
            'agent card from the specialist automatically.'
        ),
    )


@router.get('/specialists', response_model=list[SpecialistSummary])
async def list_specialists(request: Request) -> list[SpecialistSummary]:
    return await get_registry(request).list_summaries()


@router.post('/specialists', response_model=SpecialistRegistrationResponse)
async def register_specialist(
    payload: SpecialistRegistrationRequest,
    request: Request,
) -> SpecialistRegistrationResponse:
    registry = get_registry(request)
    try:
        record = await registry.register(payload)
    except Exception as exc:  # pragma: no cover - surface resolver failure
        raise HTTPException(
            status_code=400,
            detail=f'Failed to register specialist: {exc}',
        ) from exc

    return SpecialistRegistrationResponse(
        specialist=registry.to_summary(record),
        fetched_agent_card=record.agent_card,
    )


@router.post('/specialists/{specialist_id}/refresh', response_model=SpecialistRefreshResponse)
async def refresh_specialist(
    specialist_id: str,
    request: Request,
) -> SpecialistRefreshResponse:
    registry = get_registry(request)
    try:
        record = await registry.refresh_by_id(specialist_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover - surface resolver failure
        raise HTTPException(
            status_code=400,
            detail=f'Failed to refresh specialist agent card: {exc}',
        ) from exc

    return SpecialistRefreshResponse(
        specialist=registry.to_summary(record),
        refreshed_agent_card=record.agent_card,
    )


@router.post('/specialists/refresh-all', response_model=list[SpecialistSummary])
async def refresh_all_specialists(request: Request) -> list[SpecialistSummary]:
    registry = get_registry(request)
    records = await registry.refresh_all(ignore_errors=False)
    return [registry.to_summary(record) for record in records]
