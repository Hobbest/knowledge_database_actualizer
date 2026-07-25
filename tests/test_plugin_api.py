from __future__ import annotations

from types import SimpleNamespace

import pytest

from app import plugin_api
from app.config import settings
from app.llm import get_llm_provider_uncached
from app.sources import SourceDispatcher


class _EntryPoints(list):
    def select(self, *, group):
        return [point for point in self if point.group == group]


class _FakeSourceLoader:
    """Marker loader used only to verify SourceDispatcher plugin wiring."""

    def supports_path(self, path):
        return False

    def supports_url(self, url):
        return False


def test_plugin_discovery_loads_plugins_and_isolates_failures(monkeypatch):
    plugin = object()

    def fail():
        raise RuntimeError("broken plugin")

    points = _EntryPoints(
        [
            SimpleNamespace(
                name="working",
                group=plugin_api.SOURCE_LOADER_GROUP,
                load=lambda: plugin,
            ),
            SimpleNamespace(
                name="broken",
                group=plugin_api.SOURCE_LOADER_GROUP,
                load=fail,
            ),
        ]
    )
    monkeypatch.setattr(plugin_api.metadata, "entry_points", lambda: points)

    result = plugin_api.discover_entry_points(plugin_api.SOURCE_LOADER_GROUP)

    assert result.plugins == {"working": plugin}
    assert "broken" in result.failures
    assert "RuntimeError" in result.failures["broken"]


def test_discover_plugins_includes_all_groups(monkeypatch):
    monkeypatch.setattr(plugin_api.metadata, "entry_points", lambda: _EntryPoints([]))
    groups = plugin_api.discover_plugins()
    assert set(groups) == set(plugin_api.PLUGIN_GROUPS)


def test_plugin_status_summary_counts(monkeypatch):
    plugin = object()
    points = _EntryPoints(
        [
            SimpleNamespace(
                name="embed",
                group=plugin_api.EMBEDDING_BACKEND_GROUP,
                load=lambda: plugin,
            )
        ]
    )
    monkeypatch.setattr(plugin_api.metadata, "entry_points", lambda: points)
    summary = plugin_api.plugin_status_summary()
    assert summary[plugin_api.EMBEDDING_BACKEND_GROUP]["plugin_count"] == 1


def test_source_dispatcher_skips_plugins_when_discovery_disabled(monkeypatch):
    points = _EntryPoints(
        [
            SimpleNamespace(
                name="fake",
                group=plugin_api.SOURCE_LOADER_GROUP,
                load=lambda: _FakeSourceLoader,
            )
        ]
    )
    monkeypatch.setattr(plugin_api.metadata, "entry_points", lambda: points)
    monkeypatch.setattr(settings, "disable_plugin_discovery", True)
    monkeypatch.setattr(settings, "plugin_allowlist", "")

    dispatcher = SourceDispatcher()
    assert not any(isinstance(loader, _FakeSourceLoader) for loader in dispatcher.path_loaders)
    assert not any(isinstance(loader, _FakeSourceLoader) for loader in dispatcher.url_loaders)


class _BlockedSourceLoader(_FakeSourceLoader):
    pass


def test_source_dispatcher_honors_plugin_allowlist(monkeypatch):
    points = _EntryPoints(
        [
            SimpleNamespace(
                name="allowed",
                group=plugin_api.SOURCE_LOADER_GROUP,
                load=lambda: _FakeSourceLoader,
            ),
            SimpleNamespace(
                name="blocked",
                group=plugin_api.SOURCE_LOADER_GROUP,
                load=lambda: _BlockedSourceLoader,
            ),
        ]
    )
    monkeypatch.setattr(plugin_api.metadata, "entry_points", lambda: points)
    monkeypatch.setattr(settings, "disable_plugin_discovery", False)
    monkeypatch.setattr(settings, "plugin_allowlist", "allowed")

    dispatcher = SourceDispatcher()
    assert any(type(loader) is _FakeSourceLoader for loader in dispatcher.path_loaders)
    assert not any(isinstance(loader, _BlockedSourceLoader) for loader in dispatcher.path_loaders)


def test_llm_provider_skips_plugins_when_discovery_disabled(monkeypatch):
    sentinel = object()

    def factory(*, model, api_key, settings):
        return sentinel

    points = _EntryPoints(
        [
            SimpleNamespace(
                name="custom",
                group=plugin_api.LLM_PROVIDER_GROUP,
                load=lambda: factory,
            )
        ]
    )
    monkeypatch.setattr(plugin_api.metadata, "entry_points", lambda: points)
    monkeypatch.setattr(settings, "llm_provider", "custom")
    monkeypatch.setattr(settings, "llm_model", "m")
    monkeypatch.setattr(settings, "llm_api_key", "k")
    monkeypatch.setattr(settings, "disable_plugin_discovery", True)
    monkeypatch.setattr(settings, "plugin_allowlist", "")

    with pytest.raises(ValueError, match="Unsupported LLM_PROVIDER"):
        get_llm_provider_uncached()


def test_llm_provider_honors_plugin_allowlist(monkeypatch):
    sentinel = object()

    def factory(*, model, api_key, settings):
        return sentinel

    points = _EntryPoints(
        [
            SimpleNamespace(
                name="custom",
                group=plugin_api.LLM_PROVIDER_GROUP,
                load=lambda: factory,
            )
        ]
    )
    monkeypatch.setattr(plugin_api.metadata, "entry_points", lambda: points)
    monkeypatch.setattr(settings, "llm_provider", "custom")
    monkeypatch.setattr(settings, "llm_model", "m")
    monkeypatch.setattr(settings, "llm_api_key", "k")
    monkeypatch.setattr(settings, "disable_plugin_discovery", False)
    monkeypatch.setattr(settings, "plugin_allowlist", "other")

    with pytest.raises(ValueError, match="Unsupported LLM_PROVIDER"):
        get_llm_provider_uncached()

    monkeypatch.setattr(settings, "plugin_allowlist", "custom")
    assert get_llm_provider_uncached() is sentinel