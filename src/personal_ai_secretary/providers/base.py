from typing import Protocol

from personal_ai_secretary.domain.contracts import ProviderInfo, ProviderResponse, RequestEnvelope


class AIProvider(Protocol):
    name: str

    async def health(self) -> ProviderInfo: ...

    async def generate(self, request: RequestEnvelope) -> ProviderResponse: ...
