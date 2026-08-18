from uuid import uuid4

import pytest

from personal_ai_secretary.domain.contracts import RequestEnvelope
from personal_ai_secretary.providers.deterministic import DeterministicProvider


@pytest.mark.asyncio
async def test_deterministic_provider_is_available() -> None:
    provider = DeterministicProvider()
    health = await provider.health()
    assert health.available is True
    assert health.is_ai is False


@pytest.mark.asyncio
async def test_deterministic_provider_is_repeatable() -> None:
    provider = DeterministicProvider()
    request = RequestEnvelope(
        request_id=uuid4(),
        session_id=uuid4(),
        user_id="test",
        input="hello",
        correlation_id="c1",
    )
    first = await provider.generate(request)
    second = await provider.generate(request)
    assert first.text == second.text


@pytest.mark.asyncio
async def test_ollama_health_reports_unavailable_when_server_is_down(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from personal_ai_secretary.providers.ollama import OllamaProvider
    from personal_ai_secretary.shared.config import get_settings

    monkeypatch.setattr(get_settings(), "ollama_base_url", "http://127.0.0.1:1")
    health = await OllamaProvider().health()
    assert health.available is False
    assert health.is_ai is True


@pytest.mark.asyncio
async def test_ollama_health_reports_configured_model(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    from personal_ai_secretary.providers.ollama import OllamaProvider
    from personal_ai_secretary.shared.config import get_settings

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, url: str):
            return httpx.Response(
                200,
                request=httpx.Request("GET", url),
                json={"models": [{"name": get_settings().ollama_model}]},
            )

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: FakeClient())
    health = await OllamaProvider().health()
    assert health.available is True
    assert "available" in health.detail


@pytest.mark.asyncio
async def test_ollama_generate_returns_model_response(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    from personal_ai_secretary.domain.contracts import RequestEnvelope
    from personal_ai_secretary.providers.ollama import OllamaProvider

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(
            self, url: str, json: dict[str, object], headers: dict[str, str] | None = None
        ):
            assert "chat" in url
            messages = json.get("messages", [])
            assert len(messages) >= 1
            assert messages[-1]["role"] == "user"
            assert messages[-1]["content"] == "hello"
            return httpx.Response(
                200,
                request=httpx.Request("POST", url),
                json={"message": {"content": "local answer"}},
            )

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: FakeClient())
    request = RequestEnvelope(user_id="test", input="hello", correlation_id="corr")
    result = await OllamaProvider().generate(request)
    assert result.text == "local answer"
    assert result.provider == "ollama"


@pytest.mark.asyncio
async def test_ollama_generate_sends_conversation_history(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    from personal_ai_secretary.domain.contracts import ConversationTurn, RequestEnvelope
    from personal_ai_secretary.providers.ollama import OllamaProvider

    captured_messages: list[dict[str, str]] = []

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(
            self, url: str, json: dict[str, object], headers: dict[str, str] | None = None
        ):
            captured_messages.extend(json.get("messages", []))
            return httpx.Response(
                200,
                request=httpx.Request("POST", url),
                json={"message": {"content": "answer"}},
            )

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: FakeClient())
    request = RequestEnvelope(
        user_id="test",
        input="what about the next step?",
        correlation_id="corr",
        messages=[
            ConversationTurn(role="user", content="hello"),
            ConversationTurn(role="assistant", content="hi there"),
        ],
    )
    result = await OllamaProvider().generate(request)
    assert result.text == "answer"
    assert len(captured_messages) == 3
    assert captured_messages[0] == {"role": "user", "content": "hello"}
    assert captured_messages[1] == {"role": "assistant", "content": "hi there"}
    assert captured_messages[2] == {"role": "user", "content": "what about the next step?"}


@pytest.mark.asyncio
async def test_nvidia_health_reports_unavailable_without_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from personal_ai_secretary.providers.remote import NVIDIAProvider
    from personal_ai_secretary.shared.config import get_settings

    monkeypatch.setattr(get_settings(), "nvidia_api_key", None)
    health = await NVIDIAProvider().health()
    assert health.available is False
    assert health.is_ai is True


@pytest.mark.asyncio
async def test_nvidia_health_reports_available_with_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from personal_ai_secretary.providers.remote import NVIDIAProvider
    from personal_ai_secretary.shared.config import get_settings

    monkeypatch.setattr(get_settings(), "nvidia_api_key", "nvidia-test-key")
    health = await NVIDIAProvider().health()
    assert health.available is True
    assert "Configured" in health.detail


@pytest.mark.asyncio
async def test_nvidia_generate_requires_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    from personal_ai_secretary.providers.remote import NVIDIAProvider
    from personal_ai_secretary.shared.config import get_settings

    monkeypatch.setattr(get_settings(), "nvidia_api_key", None)
    request = RequestEnvelope(user_id="test", input="hello", correlation_id="corr")
    with pytest.raises(RuntimeError, match="not configured"):
        await NVIDIAProvider().generate(request)


@pytest.mark.asyncio
async def test_nvidia_generate_sends_bearer_and_returns_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import httpx

    from personal_ai_secretary.providers.remote import NVIDIAProvider
    from personal_ai_secretary.shared.config import get_settings

    captured_headers: dict[str, str] = {}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(
            self, url: str, json: dict[str, object], headers: dict[str, str] | None = None
        ):
            captured_headers.update(headers or {})
            return httpx.Response(
                200,
                request=httpx.Request("POST", url),
                json={"choices": [{"message": {"content": "nvidia answer"}}]},
            )

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: FakeClient())
    monkeypatch.setattr(get_settings(), "nvidia_api_key", "nvidia-test-key")
    request = RequestEnvelope(user_id="test", input="hello", correlation_id="corr")
    result = await NVIDIAProvider().generate(request)
    assert result.text == "nvidia answer"
    assert result.provider == "nvidia"
    assert result.model == get_settings().nvidia_model
    assert captured_headers.get("Authorization") == "Bearer nvidia-test-key"


@pytest.mark.asyncio
async def test_nvidia_generate_propagates_traceparent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import httpx

    from personal_ai_secretary.observability.tracing import (
        init_tracing,
        shutdown_tracing,
        start_span,
    )
    from personal_ai_secretary.providers.remote import NVIDIAProvider
    from personal_ai_secretary.shared.config import get_settings

    captured_headers: dict[str, str] = {}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(
            self, url: str, json: dict[str, object], headers: dict[str, str] | None = None
        ):
            captured_headers.update(headers or {})
            return httpx.Response(
                200,
                request=httpx.Request("POST", url),
                json={"choices": [{"message": {"content": "nvidia answer"}}]},
            )

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: FakeClient())
    monkeypatch.setattr(get_settings(), "nvidia_api_key", "nvidia-test-key")
    request = RequestEnvelope(user_id="test", input="hello", correlation_id="corr")
    init_tracing(enabled=True, exporter_endpoint=None)
    try:
        with start_span("provider.run"):
            await NVIDIAProvider().generate(request)
    finally:
        shutdown_tracing()

    assert "traceparent" in captured_headers
    assert captured_headers["traceparent"].startswith("00-")


@pytest.mark.asyncio
async def test_nvidia_generate_sends_conversation_history(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    from personal_ai_secretary.domain.contracts import ConversationTurn, RequestEnvelope
    from personal_ai_secretary.providers.remote import NVIDIAProvider
    from personal_ai_secretary.shared.config import get_settings

    captured_messages: list[dict[str, str]] = []

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(
            self, url: str, json: dict[str, object], headers: dict[str, str] | None = None
        ):
            captured_messages.extend(json.get("messages", []))
            return httpx.Response(
                200,
                request=httpx.Request("POST", url),
                json={"choices": [{"message": {"content": "nvidia reply"}}]},
            )

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: FakeClient())
    monkeypatch.setattr(get_settings(), "nvidia_api_key", "nvidia-test-key")
    request = RequestEnvelope(
        user_id="test",
        input="what about the next step?",
        correlation_id="corr",
        messages=[
            ConversationTurn(role="user", content="hello"),
            ConversationTurn(role="assistant", content="hi there"),
        ],
    )
    result = await NVIDIAProvider().generate(request)
    assert result.text == "nvidia reply"
    assert len(captured_messages) == 3
    assert captured_messages[0] == {"role": "user", "content": "hello"}
    assert captured_messages[1] == {"role": "assistant", "content": "hi there"}
    assert captured_messages[2] == {"role": "user", "content": "what about the next step?"}


@pytest.mark.asyncio
async def test_ollama_generate_injects_context_summary_as_system_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import httpx

    from personal_ai_secretary.domain.contracts import ConversationTurn, RequestEnvelope
    from personal_ai_secretary.providers.ollama import OllamaProvider

    captured_messages: list[dict[str, str]] = []

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(
            self, url: str, json: dict[str, object], headers: dict[str, str] | None = None
        ):
            captured_messages.extend(json.get("messages", []))
            return httpx.Response(
                200,
                request=httpx.Request("POST", url),
                json={"message": {"content": "context-aware answer"}},
            )

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: FakeClient())
    request = RequestEnvelope(
        user_id="test",
        input="what did we discuss?",
        correlation_id="corr",
        context_summary=(
            "Previous context: user prefers concise replies; "
            "Research evidence: discussed project timeline"
        ),
        messages=[
            ConversationTurn(role="user", content="tell me about the project"),
            ConversationTurn(role="assistant", content="the project is on track"),
        ],
    )
    result = await OllamaProvider().generate(request)
    assert result.text == "context-aware answer"
    assert captured_messages[0] == {
        "role": "system",
        "content": (
            "Previous context: user prefers concise replies; "
            "Research evidence: discussed project timeline"
        ),
    }
    assert captured_messages[1] == {"role": "user", "content": "tell me about the project"}
    assert captured_messages[2] == {"role": "assistant", "content": "the project is on track"}
    assert captured_messages[3] == {"role": "user", "content": "what did we discuss?"}


@pytest.mark.asyncio
async def test_ollama_generate_no_system_message_without_context_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import httpx

    from personal_ai_secretary.providers.ollama import OllamaProvider

    captured_messages: list[dict[str, str]] = []

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(
            self, url: str, json: dict[str, object], headers: dict[str, str] | None = None
        ):
            captured_messages.extend(json.get("messages", []))
            return httpx.Response(
                200,
                request=httpx.Request("POST", url),
                json={"message": {"content": "simple answer"}},
            )

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: FakeClient())
    request = RequestEnvelope(
        user_id="test",
        input="hello",
        correlation_id="corr",
        context_summary=None,
    )
    await OllamaProvider().generate(request)
    assert all(msg["role"] != "system" for msg in captured_messages)


@pytest.mark.asyncio
async def test_nvidia_generate_injects_context_summary_as_system_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import httpx

    from personal_ai_secretary.providers.remote import NVIDIAProvider
    from personal_ai_secretary.shared.config import get_settings

    captured_messages: list[dict[str, str]] = []

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(
            self, url: str, json: dict[str, object], headers: dict[str, str] | None = None
        ):
            captured_messages.extend(json.get("messages", []))
            return httpx.Response(
                200,
                request=httpx.Request("POST", url),
                json={"choices": [{"message": {"content": "nvidia context answer"}}]},
            )

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: FakeClient())
    monkeypatch.setattr(get_settings(), "nvidia_api_key", "nvidia-test-key")
    request = RequestEnvelope(
        user_id="test",
        input="what did we discuss?",
        correlation_id="corr",
        context_summary="Memory: user prefers Python; Evidence: API design guidelines",
    )
    result = await NVIDIAProvider().generate(request)
    assert result.text == "nvidia context answer"
    assert captured_messages[0] == {
        "role": "system",
        "content": "Memory: user prefers Python; Evidence: API design guidelines",
    }
    assert captured_messages[1] == {"role": "user", "content": "what did we discuss?"}
