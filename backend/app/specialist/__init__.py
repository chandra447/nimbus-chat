"""Nimbus Chat specialist framework (app-level, built on the nimbus_a2a SDK).

This package holds the shared LangChain + Tavily specialist framework
(:mod:`config`, :mod:`executor`, :mod:`agent_card`, :mod:`tavily`,
:mod:`service`) and one subpackage per concrete specialist:

- :mod:`app.specialist.travel`    — Nimbus Travel Planner
- :mod:`app.specialist.nutrition` — Nimbus Nutritionist

The :data:`SPECIALIST_CONFIGS` registry and :func:`get_specialist_config`
resolve a specialist by its type name (``SPECIALIST_TYPE`` env var).
"""

from __future__ import annotations

from app.specialist.config import SpecialistConfig, SpecialistSkillSpec
from app.specialist.nutrition import nutrition_config
from app.specialist.travel import travel_config

# Registry of all specialist configs by type name.
SPECIALIST_CONFIGS: dict[str, SpecialistConfig] = {
    'travel': travel_config,
    'nutrition': nutrition_config,
}


def get_specialist_config(specialist_type: str) -> SpecialistConfig:
    """Look up a specialist config by type name (e.g. 'travel', 'nutrition')."""
    config = SPECIALIST_CONFIGS.get(specialist_type)
    if config is None:
        raise ValueError(
            f'Unknown specialist type: {specialist_type!r}. '
            f'Available: {", ".join(SPECIALIST_CONFIGS)}'
        )
    return config


__all__ = [
    'SpecialistConfig',
    'SpecialistSkillSpec',
    'travel_config',
    'nutrition_config',
    'SPECIALIST_CONFIGS',
    'get_specialist_config',
]
