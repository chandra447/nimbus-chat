from __future__ import annotations

from collections.abc import Awaitable, Callable

from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langchain.messages import SystemMessage

from app.orchestrator.registry import SpecialistRegistry


class RegisteredSpecialistPromptMiddleware(AgentMiddleware):
    def __init__(self, registry: SpecialistRegistry) -> None:
        self.registry = registry

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        specialist_prompt = await self.registry.render_prompt_fragment()
        existing_blocks: list[dict] = list(request.system_message.content_blocks or [])
        existing_blocks.append(
            {
                'type': 'text',
                'text': (
                    'Use the following registered specialist agent cards as routing context. '
                    'When a specialist is clearly relevant, prefer routing the task instead of answering blindly.\n\n'
                    f'{specialist_prompt}'
                ),
            }
        )
        system_message = SystemMessage(content=existing_blocks)
        return await handler(request.override(system_message=system_message))

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        raise NotImplementedError(
            'RegisteredSpecialistPromptMiddleware only supports async agent invocation.'
        )
