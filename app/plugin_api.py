from __future__ import annotations

import logging
from dataclasses import dataclass, field
from importlib import metadata
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)

SOURCE_LOADER_GROUP = "actualizer.source_loaders"
LLM_PROVIDER_GROUP = "actualizer.llm_providers"
EMBEDDING_BACKEND_GROUP = "actualizer.embedding_backends"
VECTOR_STORE_GROUP = "actualizer.vector_stores"
PROMPT_DOMAIN_GROUP = "actualizer.prompt_domains"

PLUGIN_GROUPS = (
    SOURCE_LOADER_GROUP,
    LLM_PROVIDER_GROUP,
    EMBEDDING_BACKEND_GROUP,
    VECTOR_STORE_GROUP,
    PROMPT_DOMAIN_GROUP,
)


@dataclass
class PluginDiscovery:
    plugins: dict[str, Any] = field(default_factory=dict)
    failures: dict[str, str] = field(default_factory=dict)

    @property
    def plugin_count(self) -> int:
        return len(self.plugins)


@runtime_checkable
class EmbeddingBackendFactory(Protocol):
    def __call__(self, *args: Any, **kwargs: Any) -> Any: ...


@runtime_checkable
class VectorStoreFactory(Protocol):
    def __call__(self, *args: Any, **kwargs: Any) -> Any: ...


def _entry_points_for(group: str) -> list[Any]:
    points = metadata.entry_points()
    if hasattr(points, "select"):
        return list(points.select(group=group))
    if isinstance(points, dict):
        return list(points.get(group, ()))
    return [point for point in points if getattr(point, "group", None) == group]


def _filter_plugins(discovery: PluginDiscovery, allowlist: frozenset[str]) -> PluginDiscovery:
    if not allowlist:
        return discovery
    filtered = PluginDiscovery(
        plugins={name: value for name, value in discovery.plugins.items() if name in allowlist},
        failures=dict(discovery.failures),
    )
    for name in discovery.plugins:
        if name not in allowlist:
            filtered.failures[name] = "blocked by plugin allowlist"
    return filtered


def discover_entry_points(group: str, *, allowlist: frozenset[str] | None = None) -> PluginDiscovery:
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
        if allowlist and name not in allowlist:
            discovered.failures[name] = "blocked by plugin allowlist"
            continue
        try:
            discovered.plugins[name] = point.load()
        except Exception as exc:
            discovered.failures[name] = f"{type(exc).__name__}: {exc}"
            logger.warning("Could not load plugin %s from %s: %s", name, group, exc)
    return discovered


def discover_source_loaders(*, allowlist: frozenset[str] | None = None) -> dict[str, Any]:
    return discover_entry_points(SOURCE_LOADER_GROUP, allowlist=allowlist).plugins


def discover_llm_providers(*, allowlist: frozenset[str] | None = None) -> dict[str, Any]:
    return discover_entry_points(LLM_PROVIDER_GROUP, allowlist=allowlist).plugins


def discover_embedding_backends(*, allowlist: frozenset[str] | None = None) -> PluginDiscovery:
    return discover_entry_points(EMBEDDING_BACKEND_GROUP, allowlist=allowlist)


def discover_vector_stores(*, allowlist: frozenset[str] | None = None) -> PluginDiscovery:
    return discover_entry_points(VECTOR_STORE_GROUP, allowlist=allowlist)


def discover_prompt_domains(*, allowlist: frozenset[str] | None = None) -> PluginDiscovery:
    return discover_entry_points(PROMPT_DOMAIN_GROUP, allowlist=allowlist)


def discover_plugins(
    *,
    allowlist: frozenset[str] | None = None,
    disabled: bool = False,
) -> dict[str, PluginDiscovery]:
    if disabled:
        empty = PluginDiscovery()
        return {group: empty for group in PLUGIN_GROUPS}
    return {
        group: discover_entry_points(group, allowlist=allowlist)
        for group in PLUGIN_GROUPS
    }


def plugin_status_summary(
    *,
    allowlist: frozenset[str] | None = None,
    disabled: bool = False,
) -> dict[str, dict[str, Any]]:
    groups = discover_plugins(allowlist=allowlist, disabled=disabled)
    return {
        group: {
            "plugin_count": discovery.plugin_count,
            "failures": dict(discovery.failures),
        }
        for group, discovery in groups.items()
    }
