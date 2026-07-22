from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path

from app.config import settings
from app.plugin_api import discover_prompt_domains

logger = logging.getLogger(__name__)
DOMAIN_DIR = Path(__file__).resolve().parent.parent / "prompts" / "domains"
_SAFE_NAME = frozenset("abcdefghijklmnopqrstuvwxyz0123456789_-")


def _is_safe_domain_name(name: str) -> bool:
    cleaned = name.strip().lower()
    return bool(cleaned) and all(char in _SAFE_NAME for char in cleaned)


def _data_domain_dir() -> Path:
    return settings.data_dir / "domains"


def _rules_from_payload(payload: object) -> tuple[str, ...]:
    rules = payload.get("rules") if isinstance(payload, dict) else None
    if not isinstance(rules, list):
        return ()
    return tuple(str(rule).strip() for rule in rules if str(rule).strip())


@lru_cache(maxsize=32)
def load_domain_rules(domain: str) -> tuple[str, ...]:
    """Load a prompt pack from bundled files, DATA_DIR/domains, or entry-point plugins."""
    name = domain.strip().lower()
    if not _is_safe_domain_name(name):
        return ()

    bundled = DOMAIN_DIR / f"{name}.json"
    if bundled.is_file():
        try:
            payload = json.loads(bundled.read_text(encoding="utf-8"))
            rules = _rules_from_payload(payload)
            if rules:
                return rules
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Could not load bundled prompt domain %r: %s", name, exc)

    data_path = _data_domain_dir() / f"{name}.json"
    if data_path.is_file():
        try:
            payload = json.loads(data_path.read_text(encoding="utf-8"))
            rules = _rules_from_payload(payload)
            if rules:
                return rules
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Could not load data-dir prompt domain %r: %s", name, exc)

    if settings.disable_plugin_discovery:
        return ()

    plugins = discover_prompt_domains(allowlist=settings.plugin_allowlist_set or None)
    loader = plugins.plugins.get(name)
    if loader is None:
        return ()
    try:
        payload = loader() if callable(loader) else loader
        if isinstance(payload, dict):
            return _rules_from_payload(payload)
        if isinstance(payload, (list, tuple)):
            return tuple(str(rule).strip() for rule in payload if str(rule).strip())
    except Exception as exc:
        logger.warning("Prompt domain plugin %r failed: %s", name, exc)
    return ()


def selected_domain_rules() -> tuple[str, ...]:
    return load_domain_rules(settings.prompt_domain)
