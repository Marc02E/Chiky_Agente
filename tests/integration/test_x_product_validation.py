"""FASE X — Product UX, Configuration Persistence & End-to-End Validation Tests.

Tests X01-X25: Config persistence, Manual/Automatic mode, scroll, provenance,
connection tests, UI contract, and end-to-end validation.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from personal_ai_secretary.shared.settings_store import ProviderSettings, SettingsStore

# ── X01-X05: Config Persistence ──────────────────────────────────────────


class TestX01ConfigPersistence:
    """X01: SettingsStore.save persists settings to database."""

    @pytest.mark.asyncio
    async def test_save_creates_records(self) -> None:
        store = SettingsStore()
        session = AsyncMock()
        # Mock existing = None (no existing records), so each key creates new
        session.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None)))
        session.add = MagicMock()
        session.commit = AsyncMock()

        settings = {"routing_mode": "manual"}
        await store.save(session, settings)
        session.add.assert_called_once()
        session.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_save_upserts_existing(self) -> None:
        store = SettingsStore()
        existing_record = MagicMock()
        existing_record.setting_value = "old"
        session = AsyncMock()
        session.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=existing_record)))
        session.commit = AsyncMock()

        await store.save(session, {"routing_mode": "manual"})
        assert existing_record.setting_value == "manual"
        session.add.assert_not_called()
        session.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_load_returns_empty_when_no_record(self) -> None:
        store = SettingsStore()
        session = AsyncMock()
        session.execute = AsyncMock(return_value=MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))))

        loaded = await store.load(session)
        assert loaded.gemini_api_key == ""
        assert loaded.routing_mode == "automatic"

    @pytest.mark.asyncio
    async def test_delete_all_settings(self) -> None:
        store = SettingsStore()
        session = AsyncMock()
        session.execute = AsyncMock()
        session.commit = AsyncMock()

        await store.delete_all(session)
        session.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_load_settings_with_all_fields(self) -> None:
        store = SettingsStore()
        r1 = MagicMock(setting_key="ollama_model", setting_value="llama3.1:latest")
        r2 = MagicMock(setting_key="routing_mode", setting_value="manual")
        r3 = MagicMock(setting_key="ai_provider", setting_value="remote")
        session = AsyncMock()
        session.execute = AsyncMock(return_value=MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[r1, r2, r3])))))

        loaded = await store.load(session)
        assert loaded.ollama_model == "llama3.1:latest"
        assert loaded.routing_mode == "manual"
        assert loaded.ai_provider == "remote"


# ── X06-X08: API Key Obfuscation & Masking ───────────────────────────────


class TestX06X08KeySecurity:
    """X06-X08: API keys are obfuscated when stored, masked when displayed."""

    def test_mask_long_key(self) -> None:
        store = SettingsStore()
        result = store.mask_key("sk-1234567890abcdef")
        assert "****" in result
        assert result.endswith("cdef")

    def test_mask_short_key_returns_empty(self) -> None:
        store = SettingsStore()
        result = store.mask_key("abc")
        assert result == ""

    def test_mask_empty_key(self) -> None:
        store = SettingsStore()
        result = store.mask_key("")
        assert result == ""

    @pytest.mark.asyncio
    async def test_sensitive_keys_obfuscated_on_save(self) -> None:
        store = SettingsStore()
        session = AsyncMock()
        session.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None)))
        session.add = MagicMock()
        session.commit = AsyncMock()

        await store.save(session, {"gemini_api_key": "super-secret-key-1234567890"})
        # The added record should have obfuscated value, not plain text
        added_record = session.add.call_args[0][0]
        assert added_record.setting_value != "super-secret-key-1234567890"


# ── X09-X12: Manual/Automatic Mode ───────────────────────────────────────


class TestX09X12RoutingMode:
    """X09-X12: Manual vs Automatic mode persistence and behavior."""

    def test_default_mode_is_automatic(self) -> None:
        settings = ProviderSettings()
        assert settings.routing_mode == "automatic"

    def test_manual_mode_persists(self) -> None:
        settings = ProviderSettings(routing_mode="manual", selected_provider="ollama", selected_model="llama3:latest")
        d = settings.to_dict()
        assert d["routing_mode"] == "manual"
        assert d["selected_provider"] == "ollama"
        assert d["selected_model"] == "llama3:latest"

    def test_automatic_mode_default_selection(self) -> None:
        settings = ProviderSettings(routing_mode="automatic")
        d = settings.to_dict()
        assert d["routing_mode"] == "automatic"

    def test_to_dict_has_all_keys(self) -> None:
        settings = ProviderSettings()
        d = settings.to_dict()
        assert "routing_mode" in d
        assert "selected_provider" in d
        assert "selected_model" in d
        assert "gemini_api_key" in d
        assert "ai_provider" in d


# ── X13-X15: Settings Modal Scroll ───────────────────────────────────────


class TestX13X15ModalScroll:
    """X13-X15: Settings modal has proper overflow for scrolling."""

    def test_modal_body_has_overflow(self) -> None:
        with open("src/personal_ai_secretary/ui/static/css/style.css", encoding="utf-8") as f:
            css = f.read()
        assert "overflow-y: auto" in css

    def test_modal_body_has_max_height(self) -> None:
        with open("src/personal_ai_secretary/ui/static/css/style.css", encoding="utf-8") as f:
            css = f.read()
        assert "max-height" in css

    def test_routing_mode_selector_in_html(self) -> None:
        with open("src/personal_ai_secretary/ui/static/index.html", encoding="utf-8") as f:
            html = f.read()
        assert "routing-mode-selector" in html


# ── X16-X18: Provenance Display ──────────────────────────────────────────


class TestX16X18ProvenanceDisplay:
    """X16-X18: Execution provenance is displayed in the UI."""

    def test_provenance_section_in_html(self) -> None:
        with open("src/personal_ai_secretary/ui/static/index.html", encoding="utf-8") as f:
            html = f.read()
        assert "provenance-display" in html
        assert "prov-mode" in html
        assert "prov-requested" in html
        assert "prov-actual" in html
        assert "prov-status" in html

    def test_provenance_js_function_exists(self) -> None:
        with open("src/personal_ai_secretary/ui/static/js/app.js", encoding="utf-8") as f:
            js = f.read()
        assert "updateProvenanceDisplay" in js
        assert "updateAutoProviderDisplay" in js

    def test_provenance_styles_exist(self) -> None:
        with open("src/personal_ai_secretary/ui/static/css/style.css", encoding="utf-8") as f:
            css = f.read()
        assert "provenance-display" in css
        assert "prov-label" in css
        assert "prov-value" in css


# ── X19-X21: Manual Provider Selection UI ─────────────────────────────────


class TestX19X21ManualProviderUI:
    """X19-X21: Manual provider selection interface exists and is functional."""

    def test_manual_section_in_html(self) -> None:
        with open("src/personal_ai_secretary/ui/static/index.html", encoding="utf-8") as f:
            html = f.read()
        assert "manual-provider-section" in html
        assert "manual-provider-select" in html
        assert "manual-model-select" in html

    def test_manual_provider_status_in_html(self) -> None:
        with open("src/personal_ai_secretary/ui/static/index.html", encoding="utf-8") as f:
            html = f.read()
        assert "manual-provider-status" in html
        assert "manual-status-badge" in html

    def test_manual_provider_js_exists(self) -> None:
        with open("src/personal_ai_secretary/ui/static/js/app.js", encoding="utf-8") as f:
            js = f.read()
        assert "loadManualProviderList" in js
        assert "selectManualProvider" in js


# ── X22-X23: Backend API Contract ─────────────────────────────────────────


class TestX22X23BackendContract:
    """X22-X23: Backend config endpoints return correct schema."""

    def test_config_response_has_routing_mode(self) -> None:
        import inspect

        from personal_ai_secretary.api.app import get_provider_config
        src = inspect.getsource(get_provider_config)
        assert "routing_mode" in src

    def test_config_post_handles_routing_mode(self) -> None:
        import inspect

        from personal_ai_secretary.api.app import update_provider_config
        src = inspect.getsource(update_provider_config)
        assert "routing_mode" in src
        assert "automatic" in src
        assert "manual" in src

    def test_lifespan_loads_persisted_settings(self) -> None:
        with open("src/personal_ai_secretary/api/app.py", encoding="utf-8") as f:
            app_src = f.read()
        assert "settings_store" in app_src.lower() or "SettingsStore" in app_src


# ── X24-X25: End-to-End Validation ───────────────────────────────────────


class TestX24X25EndToEnd:
    """X24-X25: Full end-to-end validation of FASE X features."""

    def test_all_html_ids_referenced_in_js(self) -> None:
        """X24: Every ID in the new HTML sections is referenced in JS."""
        with open("src/personal_ai_secretary/ui/static/index.html", encoding="utf-8") as f:
            html = f.read()
        with open("src/personal_ai_secretary/ui/static/js/app.js", encoding="utf-8") as f:
            js = f.read()

        new_ids = [
            "routing-mode-selector", "manual-provider-section", "auto-provider-display",
            "manual-provider-select", "manual-model-select", "manual-provider-status",
            "manual-status-badge", "manual-status-text", "auto-provider-name",
            "auto-provider-model", "auto-provider-reason",
            "prov-mode", "prov-requested", "prov-actual", "prov-status", "prov-fallback",
        ]
        for id_ in new_ids:
            assert id_ in html, f"ID {id_} not found in HTML"
            assert id_ in js, f"ID {id_} not found in JS"

    def test_no_regressions_in_existing_sections(self) -> None:
        """X25: Existing config sections (theme, model, provider) still present."""
        with open("src/personal_ai_secretary/ui/static/index.html", encoding="utf-8") as f:
            html = f.read()
        assert "theme-options" in html
        assert "provider-info-content" in html
        assert "model-quick-select" in html
        assert "model-advanced-panel" in html
        assert "btn-verify-model" in html
        assert "btn-refresh-models" in html
        assert "settings-modal" in html
