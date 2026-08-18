from personal_ai_secretary.domain.contracts import ProviderInfo, ProviderResponse, RequestEnvelope


class DeterministicProvider:
    name = "deterministic"

    async def health(self) -> ProviderInfo:
        return ProviderInfo(
            name=self.name,
            mode="deterministic",
            available=True,
            is_ai=False,
            detail="Deterministic test provider; no AI model or hosted API is used.",
        )

    async def generate(self, request: RequestEnvelope) -> ProviderResponse:
        return ProviderResponse(
            text=f"DETERMINISTIC_RESPONSE: {request.input}",
            provider=self.name,
            model=None,
        )
