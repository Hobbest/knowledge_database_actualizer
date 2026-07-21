from __future__ import annotations

from types import SimpleNamespace

from app import plugin_api


class _EntryPoints(list):
    def select(self, *, group):
        return [point for point in self if point.group == group]


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
