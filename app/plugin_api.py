from __future__ import annotations

import logging
from dataclasses import dataclass, field
from importlib import metadata
from typing import Any

logger = logging.getLogger(__name__)

SOURCE_LOADER_GROUP = "actualizer.source_loaders"
LLM_PROVIDER_GROUP = "actualizer.llm_providers"


@dataclass
class PluginDiscovery:
    plugins: dict[str, Any] = field(default_factory=dict)
    failures: dict[str, str] = field(default_factory=dict)


def _entry_points_for(group: str) -> list[Any]:
    points = metadata.entry_points()
    if hasattr(points, "select"):
        return list(points.select(group=group))
    if isinstance(points, dict):
        return list(points.get(group, ()))
    return [point for point in points if getattr(point, "group", None) == group]


def discover_entry_points(group: str) -> PluginDiscovery:
    """Load one plugin group without allowing broken plugins to stop startup."""

    discovered = PluginDiscovery()
    try:
        points = _entry_points_for(group)
    except Exception as exc:
        discovered.failures[group] = f"{type(exc).__name__}: {exc}"
        logger.warning("Could not enumerate plugin group %s: %s", group, exc)
        return discovered

    for point in points:
        name = str(getattr(point, "name", repr(point)))
        try:
            discovered.plugins[name] = point.load()
        except Exception as exc:
            discovered.failures[name] = f"{type(exc).__name__}: {exc}"
            logger.warning("Could not load plugin %s from %s: %s", name, group, exc)
    return discovered


def discover_source_loaders() -> dict[str, Any]:
    return discover_entry_points(SOURCE_LOADER_GROUP).plugins


def discover_llm_providers() -> dict[str, Any]:
    return discover_entry_points(LLM_PROVIDER_GROUP).plugins


def discover_plugins() -> dict[str, PluginDiscovery]:
    return {
        SOURCE_LOADER_GROUP: discover_entry_points(SOURCE_LOADER_GROUP),
        LLM_PROVIDER_GROUP: discover_entry_points(LLM_PROVIDER_GROUP),
    }
