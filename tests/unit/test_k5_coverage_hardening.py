"""FASE K.5 coverage hardening.

Exercises error/edge paths of existing modules that predate K.5 so the
project-wide coverage gate (>= 94%) holds with the new context package:
development tools, filesystem tools, project tool, Ollama provider, and UI
routes.
"""

from pathlib import Path
from uuid import uuid4

import httpx
import pytest

from personal_ai_secretary.tools.filesystem import _validate_path
from personal_ai_secretary.tools.registry import ToolError

# ═══════════════════════════════════════════════════════════════════════════════
# Filesystem tool edge cases
# ═══════════════════════════════════════════════════════════════════════════════


class TestFilesystemEdgeCases:
    def test_validate_path_resolve_failure(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def boom(self: Path, *, strict: bool = False) -> Path:
            raise ValueError("embedded null character")

        monkeypatch.setattr(Path, "resolve", boom)
        with pytest.raises(ToolError):
            _validate_path("some/path")

    @pytest.mark.asyncio
    async def test_read_file_latin1_fallback(self, tmp_path: Path) -> None:
        from personal_ai_secretary.tools.filesystem import _read_file

        target = tmp_path / "latin.txt"
        target.write_bytes(b"caf\xe9")  # invalid UTF-8, valid latin-1
        result = await _read_file({"path": str(target)})
        assert "error" not in result
        assert "caf" in str(result["result"])

    @pytest.mark.asyncio
    async def test_read_file_os_error(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from personal_ai_secretary.tools import filesystem as fs_mod

        target = tmp_path / "x.txt"
        target.write_text("data")

        def boom(self: Path, *a: object, **k: object) -> str:
            raise OSError("disk failure")

        monkeypatch.setattr(Path, "read_text", boom)
        result = await fs_mod._read_file({"path": str(target)})
        assert "error" in result

    @pytest.mark.asyncio
    async def test_read_file_truncation(self, tmp_path: Path) -> None:
        from personal_ai_secretary.tools.filesystem import _read_file

        target = tmp_path / "big.txt"
        target.write_text("a" * 60_000)
        result = await _read_file({"path": str(target)})
        assert result["truncated"] is True
        assert len(str(result["result"])) <= 50_020

    @pytest.mark.asyncio
    async def test_create_file_write_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from personal_ai_secretary.tools import filesystem as fs_mod

        def boom(self: Path, *a: object, **k: object) -> None:
            raise OSError("read-only filesystem")

        monkeypatch.setattr(Path, "write_text", boom)
        result = await fs_mod._create_file({"path": str(tmp_path / "n.txt"), "content": "x"})
        assert "error" in result

    @pytest.mark.asyncio
    async def test_write_file_append_mode(self, tmp_path: Path) -> None:
        from personal_ai_secretary.tools.filesystem import _write_file

        target = tmp_path / "app.txt"
        target.write_text("one")
        result = await _write_file({"path": str(target), "content": "two", "mode": "append"})
        assert "error" not in result
        assert target.read_text() == "onetwo"

    @pytest.mark.asyncio
    async def test_write_file_failure(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from personal_ai_secretary.tools import filesystem as fs_mod

        def boom(self: Path, *a: object, **k: object) -> None:
            raise OSError("no space")

        monkeypatch.setattr(Path, "write_text", boom)
        result = await fs_mod._write_file({"path": str(tmp_path / "w.txt"), "content": "x"})
        assert "error" in result

    @pytest.mark.asyncio
    async def test_list_directory_os_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from personal_ai_secretary.tools import filesystem as fs_mod

        def boom(self: Path) -> object:
            raise OSError("permission denied")

        monkeypatch.setattr(Path, "iterdir", boom)
        result = await fs_mod._list_directory({"path": str(tmp_path)})
        assert "error" in result

    @pytest.mark.asyncio
    async def test_create_directory_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from personal_ai_secretary.tools import filesystem as fs_mod

        def boom(self: Path, *a: object, **k: object) -> None:
            raise OSError("cannot create")

        monkeypatch.setattr(Path, "mkdir", boom)
        result = await fs_mod._create_directory({"path": str(tmp_path / "sub")})
        assert "error" in result

    @pytest.mark.asyncio
    async def test_file_exists_outside_roots(self) -> None:
        from personal_ai_secretary.tools.filesystem import _file_exists

        result = await _file_exists({"path": "Z:/definitely/outside/root/file.txt"})
        assert "error" in result


# ═══════════════════════════════════════════════════════════════════════════════
# Development tool edge cases
# ═══════════════════════════════════════════════════════════════════════════════


class TestDevelopmentEdgeCases:
    @pytest.mark.asyncio
    async def test_analyze_include_content_unreadable(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from personal_ai_secretary.tools.development import _analyze_project

        (tmp_path / "a.py").write_text("ok")

        original_read = Path.read_text

        def selective_boom(self: Path, *a: object, **k: object) -> str:
            if self.name == "a.py":
                raise UnicodeDecodeError("utf-8", b"", 0, 1, "bad")
            return original_read(self, *a, **k)  # type: ignore[arg-type]

        monkeypatch.setattr(Path, "read_text", selective_boom)
        result = await _analyze_project({
            "path": str(tmp_path),
            "include_content": True,
        })
        node = next(n for n in result["structure"] if n.get("name") == "a.py")
        assert node["content"] == "[binary or unreadable]"

    @pytest.mark.asyncio
    async def test_analyze_walk_permission_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from personal_ai_secretary.tools.development import _analyze_project

        locked = tmp_path / "locked"
        locked.mkdir()
        (tmp_path / "ok.py").write_text("x")

        original_iterdir = Path.iterdir

        def selective_iterdir(self: Path) -> object:
            if self.name == "locked":
                raise PermissionError("denied")
            return original_iterdir(self)

        monkeypatch.setattr(Path, "iterdir", selective_iterdir)
        result = await _analyze_project({"path": str(tmp_path)})
        names = [n["name"] for n in result["structure"]]
        assert "ok.py" in names

    @pytest.mark.asyncio
    async def test_read_files_empty_entry_and_missing(self, tmp_path: Path) -> None:
        from personal_ai_secretary.tools.development import _read_files

        result = await _read_files({"paths": ["", str(tmp_path / "missing.txt")]})
        assert result["error_count"] >= 2
        assert "Empty path in list" in result["errors"]

    @pytest.mark.asyncio
    async def test_read_files_directory_and_outside_root(self, tmp_path: Path) -> None:
        from personal_ai_secretary.tools.development import _read_files

        result = await _read_files({"paths": [str(tmp_path), "Z:/outside/file.txt"]})
        assert result["error_count"] == 2

    @pytest.mark.asyncio
    async def test_read_files_latin1_fallback(self, tmp_path: Path) -> None:
        from personal_ai_secretary.tools.development import _read_files

        target = tmp_path / "l.txt"
        target.write_bytes(b"\xff\xfe binary-ish")
        result = await _read_files({"paths": [str(target)]})
        assert result["read_count"] == 1

    @pytest.mark.asyncio
    async def test_read_files_os_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from personal_ai_secretary.tools.development import _read_files

        target = tmp_path / "x.txt"
        target.write_text("data")

        def boom(self: Path, *a: object, **k: object) -> str:
            raise OSError("locked")

        monkeypatch.setattr(Path, "read_text", boom)
        result = await _read_files({"paths": [str(target)]})
        assert result["error_count"] == 1

    @pytest.mark.asyncio
    async def test_read_files_truncates_large_file(self, tmp_path: Path) -> None:
        from personal_ai_secretary.tools.development import _read_files

        target = tmp_path / "huge.txt"
        target.write_text("b" * 60_000)
        result = await _read_files({"paths": [str(target)]})
        info = result["files"][str(target)]
        assert info["truncated"] is True

    @pytest.mark.asyncio
    async def test_modify_file_validation_errors(self, tmp_path: Path) -> None:
        from personal_ai_secretary.tools.development import _modify_file

        assert "error" in await _modify_file({"path": ""})
        assert "error" in await _modify_file({"path": "Z:/outside/x.txt", "mode": "append"})
        missing = tmp_path / "nope.txt"
        assert "error" in await _modify_file({"path": str(missing), "mode": "append"})
        assert "error" in await _modify_file({"path": str(tmp_path), "mode": "append"})

    @pytest.mark.asyncio
    async def test_modify_file_read_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from personal_ai_secretary.tools.development import _modify_file

        target = tmp_path / "m.txt"
        target.write_text("content")

        def boom(self: Path, *a: object, **k: object) -> str:
            raise OSError("cannot read")

        monkeypatch.setattr(Path, "read_text", boom)
        result = await _modify_file({"path": str(target), "mode": "append", "content": "z"})
        assert "error" in result

    @pytest.mark.asyncio
    async def test_modify_file_modes(self, tmp_path: Path) -> None:
        from personal_ai_secretary.tools.development import _modify_file

        # overwrite unchanged
        t = tmp_path / "modes.txt"
        t.write_text("same")
        result = await _modify_file({
            "path": str(t), "mode": "overwrite", "content": "same",
        })
        assert result["result"] == "unchanged"

        # append empty content is a no-op
        t.write_text("base")
        result = await _modify_file({"path": str(t), "mode": "append", "content": ""})
        assert result.get("result") == "unchanged"

        # prepend
        result = await _modify_file({
            "path": str(t), "mode": "prepend", "content": "HEAD;",
        })
        assert t.read_text().startswith("HEAD;")

        # insert_after with missing search
        result = await _modify_file({
            "path": str(t), "mode": "insert_after",
            "search": "NOT_PRESENT", "content": "x",
        })
        assert "error" in result

        # insert_before with missing search
        result = await _modify_file({
            "path": str(t), "mode": "insert_before",
            "search": "NOT_PRESENT", "content": "x",
        })
        assert "error" in result

        # unknown mode
        result = await _modify_file({"path": str(t), "mode": "teleport"})
        assert "error" in result

    @pytest.mark.asyncio
    async def test_modify_file_insert_modes_with_newline_handling(self, tmp_path: Path) -> None:
        from personal_ai_secretary.tools.development import _modify_file

        t = tmp_path / "ins.txt"
        t.write_text("alpha\nbeta\n")
        result = await _modify_file({
            "path": str(t), "mode": "insert_after",
            "search": "alpha", "content": "GAMMA",
        })
        assert "error" not in result
        assert "alpha\nGAMMA\nbeta" in t.read_text()

        result = await _modify_file({
            "path": str(t), "mode": "insert_before",
            "search": "beta", "content": "DELTA",
        })
        assert "error" not in result
        # insert_before adds no separator when preceded by a newline
        assert "DELTAbeta" in t.read_text()

    @pytest.mark.asyncio
    async def test_modify_file_write_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from personal_ai_secretary.tools.development import _modify_file

        target = tmp_path / "w.txt"
        target.write_text("content")

        real_read = Path.read_text
        calls = {"n": 0}

        def read_then_fail(self: Path, *a: object, **k: object) -> str:
            calls["n"] += 1
            if calls["n"] == 2:  # second read_text call is the... none; write fails below
                return real_read(self, *a, **k)  # type: ignore[arg-type]
            return real_read(self, *a, **k)  # type: ignore[arg-type]

        monkeypatch.setattr(Path, "read_text", read_then_fail)

        def write_boom(self: Path, *a: object, **k: object) -> None:
            raise OSError("write protected")

        monkeypatch.setattr(Path, "write_text", write_boom)
        result = await _modify_file({
            "path": str(target), "mode": "replace",
            "search": "content", "replacement": "new",
        })
        assert "error" in result

    @pytest.mark.asyncio
    async def test_search_files_validation(self, tmp_path: Path) -> None:
        from personal_ai_secretary.tools.development import _search_files

        assert "error" in await _search_files({"path": "", "pattern": "x"})
        assert "error" in await _search_files({"path": str(tmp_path), "pattern": ""})
        assert "error" in await _search_files({"path": "Z:/out/side", "pattern": "x"})
        assert "error" in await _search_files({"path": str(tmp_path / "gone"), "pattern": "x"})
        f = tmp_path / "f.txt"
        f.write_text("x")
        assert "error" in await _search_files({"path": str(f), "pattern": "x"})

    @pytest.mark.asyncio
    async def test_search_files_skips_unreadable(self, tmp_path: Path) -> None:
        from personal_ai_secretary.tools.development import _search_files

        (tmp_path / "good.py").write_text("target_line here")
        (tmp_path / ".git").mkdir()
        (tmp_path / ".git" / "hidden.py").write_text("target_line hidden")

        result = await _search_files({
            "path": str(tmp_path), "pattern": "target_line", "include": "*.py",
        })
        files = {m["file"] for m in result["matches"]}
        assert any("good.py" in f for f in files)
        assert not any(".git" in f for f in files)

    @pytest.mark.asyncio
    async def test_verify_files_edge_cases(self, tmp_path: Path) -> None:
        from personal_ai_secretary.tools.development import _verify_files

        empty: dict[str, object] = await _verify_files({"verifications": []})
        assert "error" in empty

        result = await _verify_files({"verifications": ["not-a-dict"]})
        assert result["all_passed"] is False

        result = await _verify_files({"verifications": [{"path": ""}]})
        assert result["all_passed"] is False

        result = await _verify_files({"verifications": [{"path": "Z:/out/x.txt"}]})
        assert result["all_passed"] is False

        f = tmp_path / "v.txt"
        f.write_text("hello")

        # should_exist=False but file exists
        result = await _verify_files({"verifications": [
            {"path": str(f), "should_exist": False},
        ]})
        assert result["all_passed"] is False

        # should_exist=False and missing -> pass
        result = await _verify_files({"verifications": [
            {"path": str(tmp_path / "ghost.txt"), "should_exist": False},
        ]})
        assert result["all_passed"] is True

        # content_contains miss
        result = await _verify_files({"verifications": [
            {"path": str(f), "content_contains": "absent"},
        ]})
        assert result["all_passed"] is False

        # size bounds
        result = await _verify_files({"verifications": [
            {"path": str(f), "min_size": 9999},
        ]})
        assert result["all_passed"] is False
        result = await _verify_files({"verifications": [
            {"path": str(f), "max_size": 1},
        ]})
        assert result["all_passed"] is False


# ═══════════════════════════════════════════════════════════════════════════════
# create_project edge cases
# ═══════════════════════════════════════════════════════════════════════════════


class TestCreateProjectEdgeCases:
    @pytest.mark.asyncio
    async def test_argument_validation(self, tmp_path: Path) -> None:
        from personal_ai_secretary.tools.project import _create_project

        assert "error" in await _create_project({})
        assert "error" in await _create_project({"project_name": "p"})
        assert "error" in await _create_project({
            "project_name": "p", "base_path": str(tmp_path), "files": "not-a-list",
        })

    @pytest.mark.asyncio
    async def test_bad_base_path(self) -> None:
        from personal_ai_secretary.tools.project import _create_project

        result = await _create_project({
            "project_name": "p", "base_path": "Z:/outside/here", "files": [],
        })
        assert "error" in result

    @pytest.mark.asyncio
    async def test_directories_and_invalid_specs(self, tmp_path: Path) -> None:
        from personal_ai_secretary.tools.project import _create_project

        result = await _create_project({
            "project_name": "proj",
            "base_path": str(tmp_path),
            "directories": ["src", "", 42],
            "files": [
                {"path": "src/main.py", "content": "print('hi')"},
                {"path": "", "content": "no path"},
                "not-a-dict",
                {"path": "README.md", "content": "# proj"},
            ],
        })
        assert result["files_count"] == 2
        assert result["directories_count"] == 1
        assert result["errors_count"] == 2

    @pytest.mark.asyncio
    async def test_mkdir_failure(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from personal_ai_secretary.tools.project import _create_project

        def boom(self: Path, *a: object, **k: object) -> None:
            raise OSError("cannot mkdir")

        monkeypatch.setattr(Path, "mkdir", boom)
        result = await _create_project({
            "project_name": "p", "base_path": str(tmp_path), "files": [],
        })
        assert "error" in result


# ═══════════════════════════════════════════════════════════════════════════════
# Ollama provider error handling
# ═══════════════════════════════════════════════════════════════════════════════


class _FakeResponse:
    def __init__(self, status_code: int = 200, payload: dict | None = None) -> None:
        self.status_code = status_code
        self._payload = payload or {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            request = httpx.Request("GET", "http://test")
            response = httpx.Response(self.status_code, request=request)
            raise httpx.HTTPStatusError("err", request=request, response=response)

    def json(self) -> dict:
        return self._payload


class _FakeAsyncClient:
    def __init__(
        self,
        *args: object,
        get_result: object = None,
        post_result: object = None,
        post_exc: Exception | None = None,
        get_exc: Exception | None = None,
        json_exc: bool = False,
        **kwargs: object,
    ) -> None:
        self._get_result = get_result
        self._post_result = post_result
        self._post_exc = post_exc
        self._get_exc = get_exc
        self._json_exc = json_exc

    async def __aenter__(self) -> "_FakeAsyncClient":
        return self

    async def __aexit__(self, *args: object) -> bool:
        return False

    async def get(self, url: str) -> _FakeResponse:
        if self._get_exc is not None:
            raise self._get_exc
        assert isinstance(self._get_result, _FakeResponse)
        if self._json_exc:
            raise ValueError("bad json")
        return self._get_result

    async def post(self, url: str, **kwargs: object) -> _FakeResponse:
        if self._post_exc is not None:
            raise self._post_exc
        assert isinstance(self._post_result, _FakeResponse)
        return self._post_result


def _patch_client(monkeypatch: pytest.MonkeyPatch, **client_kwargs: object) -> None:
    from personal_ai_secretary.providers import ollama as ollama_mod

    def factory(*args: object, **kwargs: object) -> _FakeAsyncClient:
        return _FakeAsyncClient(*args, **client_kwargs, **kwargs)

    monkeypatch.setattr(ollama_mod.httpx, "AsyncClient", factory)


class TestOllamaProviderErrors:
    def _provider(self) -> object:
        from personal_ai_secretary.providers.ollama import OllamaProvider

        return OllamaProvider(model="llama3.1")

    @pytest.mark.asyncio
    async def test_health_bad_json(self, monkeypatch: pytest.MonkeyPatch) -> None:
        provider = self._provider()
        _patch_client(
            monkeypatch,
            get_result=_FakeResponse(200, {"models": []}),
            json_exc=True,
        )
        info = await provider.health()  # type: ignore[attr-defined]
        assert info.available is False

    @pytest.mark.asyncio
    async def test_health_http_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        provider = self._provider()
        request = httpx.Request("GET", "http://test")
        exc = httpx.HTTPStatusError(
            "server error", request=request, response=httpx.Response(500, request=request)
        )
        _patch_client(monkeypatch, get_exc=exc)
        info = await provider.health()  # type: ignore[attr-defined]
        assert info.available is False

    @pytest.mark.asyncio
    async def test_generate_connect_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        provider = self._provider()
        _patch_client(monkeypatch, post_exc=httpx.ConnectError("refused"))
        envelope = _envelope()
        with pytest.raises(ConnectionError):
            await provider.generate(envelope)  # type: ignore[attr-defined]

    @pytest.mark.asyncio
    async def test_generate_timeout(self, monkeypatch: pytest.MonkeyPatch) -> None:
        provider = self._provider()
        _patch_client(monkeypatch, post_exc=httpx.TimeoutException("slow"))
        with pytest.raises(TimeoutError):
            await provider.generate(_envelope())  # type: ignore[attr-defined]

    @pytest.mark.asyncio
    async def test_generate_404_model_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        provider = self._provider()
        _patch_client(monkeypatch, post_result=_FakeResponse(404))
        with pytest.raises(ValueError, match="not found"):
            await provider.generate(_envelope())  # type: ignore[attr-defined]

    @pytest.mark.asyncio
    async def test_generate_other_http_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        provider = self._provider()
        _patch_client(monkeypatch, post_result=_FakeResponse(500))
        with pytest.raises(RuntimeError, match="HTTP 500"):
            await provider.generate(_envelope())  # type: ignore[attr-defined]

    @pytest.mark.asyncio
    async def test_generate_generic_http_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        provider = self._provider()
        request = httpx.Request("POST", "http://test")
        exc = httpx.HTTPStatusError(
            "bad", request=request, response=httpx.Response(418, request=request)
        )
        # Force the non-HTTPStatusError HTTPError branch by raising a plain
        # httpx.HTTPError instead.
        generic = httpx.HTTPError("transport exploded")
        generic.request = request  # type: ignore[attr-defined]
        _patch_client(monkeypatch, post_exc=generic)
        with pytest.raises(RuntimeError):
            await provider.generate(_envelope())  # type: ignore[attr-defined]
        assert exc is not None  # keep linters happy about constructed exception

    @pytest.mark.asyncio
    async def test_generate_empty_reply(self, monkeypatch: pytest.MonkeyPatch) -> None:
        provider = self._provider()
        _patch_client(
            monkeypatch,
            post_result=_FakeResponse(200, {"message": {"content": ""}}),
        )
        with pytest.raises(RuntimeError, match="empty response"):
            await provider.generate(_envelope())  # type: ignore[attr-defined]

    @pytest.mark.asyncio
    async def test_generate_success_with_traceparent(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from personal_ai_secretary.observability.tracing import start_span

        provider = self._provider()

        captured: dict[str, object] = {}

        class _HeaderClient(_FakeAsyncClient):
            async def post(self, url: str, **kwargs: object) -> _FakeResponse:
                captured.update(kwargs)
                return _FakeResponse(200, {"message": {"content": "hi there"}})

        from personal_ai_secretary.providers import ollama as ollama_mod

        def factory(*args: object, **kwargs: object) -> _HeaderClient:
            return _HeaderClient(*args, **kwargs)

        monkeypatch.setattr(ollama_mod.httpx, "AsyncClient", factory)

        with start_span("test.span"):
            response = await provider.generate(_envelope())  # type: ignore[attr-defined]
        assert response.text == "hi there"
        headers = captured.get("headers")
        assert headers is not None and "traceparent" in headers


def _envelope() -> object:
    from personal_ai_secretary.domain.contracts import RequestEnvelope

    return RequestEnvelope(
        request_id=uuid4(),
        session_id=uuid4(),
        user_id="tester",
        input="hello",
        correlation_id="corr",
    )


# ═══════════════════════════════════════════════════════════════════════════════
# UI routes (rename/delete/upload/title fallback)
# ═══════════════════════════════════════════════════════════════════════════════


class TestUIRoutesK5:
    def test_rename_session(self) -> None:
        from fastapi.testclient import TestClient

        from personal_ai_secretary.api.app import app

        with TestClient(app) as client:
            sid = str(uuid4())
            r = client.post(
                f"/api/v1/sessions/{sid}/messages",
                json={"input": "session to rename"},
                headers={"Idempotency-Key": f"rename-{sid}"},
            )
            assert r.status_code == 200
            r2 = client.patch(f"/api/v1/ui/sessions/{sid}", json={"title": "Renamed!"})
            assert r2.status_code == 200
            assert r2.json()["title"] == "Renamed!"

    def test_rename_session_not_found(self) -> None:
        from fastapi.testclient import TestClient

        from personal_ai_secretary.api.app import app

        with TestClient(app) as client:
            r = client.patch(f"/api/v1/ui/sessions/{uuid4()}", json={"title": "X"})
            assert r.status_code == 404

    def test_delete_session(self) -> None:
        from fastapi.testclient import TestClient

        from personal_ai_secretary.api.app import app

        with TestClient(app) as client:
            sid = str(uuid4())
            r = client.post(
                f"/api/v1/sessions/{sid}/messages",
                json={"input": "session to delete"},
                headers={"Idempotency-Key": f"delete-{sid}"},
            )
            assert r.status_code == 200
            r2 = client.delete(f"/api/v1/ui/sessions/{sid}")
            assert r2.status_code == 204
            r3 = client.delete(f"/api/v1/ui/sessions/{sid}")
            assert r3.status_code == 404

    def test_upload_text_file(self) -> None:
        from fastapi.testclient import TestClient

        from personal_ai_secretary.api.app import app

        with TestClient(app) as client:
            r = client.post(
                "/api/v1/ui/upload",
                files={"file": ("notes.txt", b"hello world", "text/plain")},
            )
            assert r.status_code == 200
            data = r.json()
            assert data["name"] == "notes.txt"
            assert data["size"] == 11

    def test_upload_rejects_binary_type(self) -> None:
        from fastapi.testclient import TestClient

        from personal_ai_secretary.api.app import app

        with TestClient(app) as client:
            r = client.post(
                "/api/v1/ui/upload",
                files={"file": ("prog.exe", b"MZ...", "application/octet-stream")},
            )
            assert r.status_code == 415

    def test_upload_rejects_too_large(self) -> None:
        from fastapi.testclient import TestClient

        from personal_ai_secretary.api.app import app

        with TestClient(app) as client:
            r = client.post(
                "/api/v1/ui/upload",
                files={"file": ("big.txt", b"x" * 6000, "text/plain")},
            )
            assert r.status_code == 413

    def test_session_title_fallback_new_conversation(self) -> None:
        """A session row without requests falls back to 'New conversation'."""
        from fastapi.testclient import TestClient

        from personal_ai_secretary.api.app import app
        from personal_ai_secretary.domain.models import SessionRecord
        from personal_ai_secretary.infrastructure.database import get_session_factory

        with TestClient(app) as client:
            session_factory = get_session_factory()

            async def _seed() -> None:
                async with session_factory() as session:
                    session.add(SessionRecord(
                        session_id=uuid4(), user_id="development-user", request_count=0,
                    ))
                    await session.commit()

            import asyncio

            asyncio.get_event_loop_policy()
            asyncio.run(_seed())
            r = client.get("/api/v1/ui/sessions")
            assert r.status_code == 200
            titles = [s.get("title") for s in r.json()["sessions"]]
            assert any(t == "New conversation" for t in titles)
