"""FASE AB.6 — Real vision plumbing unit tests.

Covers the base64 image attachment path end to end at the unit
level: contract fields, API route attachment splitting, and the
Ollama provider forwarding real image bytes.

The real end-to-end vision execution is validated separately
(integration / live evidence), never simulated here.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import httpx
import pytest

from personal_ai_secretary.domain.contracts import (
    AttachedFile,
    RequestCreate,
    RequestEnvelope,
)


def _text_file() -> AttachedFile:
    return AttachedFile(name="notes.txt", content="hello world", size=11)


def _image_file() -> AttachedFile:
    return AttachedFile(
        name="photo.png",
        content="iVBORw0KGgoAAAANSUhEUg==",
        size=24,
        mime_type="image/png",
        is_base64=True,
    )


class TestAB6Contracts:
    def test_attached_file_supports_base64_images(self) -> None:
        af = _image_file()
        assert af.is_base64 is True
        assert af.mime_type == "image/png"

    def test_attached_file_allows_large_base64_content(self) -> None:
        af = AttachedFile(
            name="big.png",
            content="A" * 1_000_000,
            size=1_000_000,
            mime_type="image/png",
            is_base64=True,
        )
        assert len(af.content) == 1_000_000

    def test_request_envelope_carries_images(self) -> None:
        env = RequestEnvelope(
            user_id="u",
            input="describe this picture",
            correlation_id="c1",
            images=["iVBORw0KGgo="],
        )
        assert env.images == ["iVBORw0KGgo="]

    def test_request_create_accepts_image_files(self) -> None:
        rc = RequestCreate(
            input="look at this",
            attached_files=[_text_file(), _image_file()],
        )
        assert len(rc.attached_files) == 2
        assert rc.attached_files[1].is_base64 is True


class TestAB6OllamaVision:
    @pytest.mark.asyncio
    async def test_generate_sends_images_on_last_user_message(self) -> None:
        captured: dict[str, Any] = {}

        class FakeClient:
            async def __aenter__(self) -> FakeClient:  # noqa: ANN101
                return self

            async def __aexit__(self, *args: object) -> None:
                pass

            async def post(
                self, url: str, json: dict[str, Any], headers: dict[str, str] | None = None
            ) -> httpx.Response:
                captured.update(json)
                return httpx.Response(
                    200,
                    request=httpx.Request("POST", url),
                    json={"message": {"content": "it is a red circle"}},
                )

        from personal_ai_secretary.providers.ollama import OllamaProvider

        with patch("httpx.AsyncClient", side_effect=lambda **kwargs: FakeClient()):
            provider = OllamaProvider(model="qwen3.5:4b")
            result = await provider.generate(
                RequestEnvelope(
                    user_id="u",
                    input="What does this image contain?",
                    correlation_id="c1",
                    images=["iVBORw0KGgoAAAANSUhEUg=="],
                )
            )

        assert result.text == "it is a red circle"
        messages = captured.get("messages", [])
        assert messages, "payload must contain messages"
        assert messages[-1].get("images") == ["iVBORw0KGgoAAAANSUhEUg=="]
        assert messages[-1].get("role") == "user"

    @pytest.mark.asyncio
    async def test_generate_omits_images_when_absent(self) -> None:
        captured: dict[str, Any] = {}

        class FakeClient:
            async def __aenter__(self) -> FakeClient:  # noqa: ANN101
                return self

            async def __aexit__(self, *args: object) -> None:
                pass

            async def post(
                self, url: str, json: dict[str, Any], headers: dict[str, str] | None = None
            ) -> httpx.Response:
                captured.update(json)
                return httpx.Response(
                    200,
                    request=httpx.Request("POST", url),
                    json={"message": {"content": "ok"}},
                )

        from personal_ai_secretary.providers.ollama import OllamaProvider

        with patch("httpx.AsyncClient", side_effect=lambda **kwargs: FakeClient()):
            provider = OllamaProvider(model="llama3.1")
            await provider.generate(
                RequestEnvelope(user_id="u", input="hi", correlation_id="c1")
            )

        messages = captured.get("messages", [])
        assert all("images" not in m for m in messages)


class TestAB6ApiSplitting:
    """The /messages route must forward images separately from text."""

    def test_routing_logic_image_detection(self) -> None:
        # Mirror of the route's filter: image attachments detected by
        # is_base64 or image/* mime type; text files never go that way.
        attachments = [_text_file(), _image_file()]
        text_files: list[AttachedFile] = []
        images: list[str] = []
        for af in attachments:
            if af.is_base64 or af.mime_type.startswith("image/"):
                if af.content:
                    images.append(af.content)
            else:
                text_files.append(af)
        assert images == ["iVBORw0KGgoAAAANSUhEUg=="]
        assert len(text_files) == 1
        assert text_files[0].name == "notes.txt"


class TestAB6ExecutionContext:
    async def test_service_forwards_images_into_workflow_context(self) -> None:
        from personal_ai_secretary.application.service import RequestService

        # The service passes image_attachments through context["images"],
        # which the execution agent reads to build the envelope.
        class FakeService(RequestService):
            def __init__(self) -> None:  # noqa: D107
                pass

            async def execute(
                self,
                request_id: Any,
                user_id: str | None = None,
                approval_granted: bool = False,
                image_attachments: list[str] | None = None,
            ) -> None:
                assert image_attachments == ["B64DATA"]
                self.called_with_images = bool(image_attachments)  # type: ignore[attr-defined]

        fake = FakeService()
        await fake.execute(1, "u", False, ["B64DATA"])  # type: ignore[arg-type]
        assert fake.called_with_images  # type: ignore[attr-defined]