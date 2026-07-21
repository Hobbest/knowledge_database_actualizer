from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path

from app.config import settings

logger = logging.getLogger(__name__)
DOMAIN_DIR = Path(__file__).resolve().parent.parent / "prompts" / "domains"
_SAFE_NAME = frozenset("abcdefghijklmnopqrstuvwxyz0123456789_-")


@lru_cache(maxsize=16)
def load_domain_rules(domain: str) -> tuple[str, ...]:
    """Load a bundled prompt pack; invalid/missing packs safely yield no rules."""
    name = domain.strip().lower()
    if not name or any(char not in _SAFE_NAME for char in name):
        return ()
    path = DOMAIN_DIR / f"{name}.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Could not load prompt domain %r: %s", name, exc)
        return ()
    rules = payload.get("rules") if isinstance(payload, dict) else None
    if not isinstance(rules, list):
        return ()
    return tuple(str(rule).strip() for rule in rules if str(rule).strip())


def selected_domain_rules() -> tuple[str, ...]:
    return load_domain_rules(settings.prompt_domain)
