from __future__ import annotations

import re
from dataclasses import dataclass

_NUMBER = re.compile(r"(?<!\w)[+-]?\d+(?:[.,]\d+)?%?")
_YEAR = re.compile(r"\b(?:19|20)\d{2}\b")
_NEGATION = re.compile(r"\b(?:no|not|never|neither|without|cannot|can't|doesn't|isn't|wasn't)\b", re.I)


@dataclass(frozen=True)
class UpdateDetection:
    update_type: str | None = None
    update_reason: str | None = None


def detect_update(source_text: str, vault_text: str, *, similarity: float = 0.0) -> UpdateDetection:
    """Flag likely updates/contradictions in otherwise similar passages."""
    if similarity < 0.5 or not source_text.strip() or not vault_text.strip():
        return UpdateDetection()

    source_negated = bool(_NEGATION.search(source_text))
    vault_negated = bool(_NEGATION.search(vault_text))
    if source_negated != vault_negated:
        return UpdateDetection("contradiction", "The source and vault passage differ in negation.")

    source_years, vault_years = set(_YEAR.findall(source_text)), set(_YEAR.findall(vault_text))
    if source_years and vault_years and source_years != vault_years:
        return UpdateDetection(
            "update",
            f"Date values differ ({', '.join(sorted(vault_years))} → "
            f"{', '.join(sorted(source_years))}).",
        )

    source_numbers = set(_NUMBER.findall(_YEAR.sub("", source_text)))
    vault_numbers = set(_NUMBER.findall(_YEAR.sub("", vault_text)))
    if source_numbers and vault_numbers and source_numbers != vault_numbers:
        return UpdateDetection("update", "Numeric values differ from the closest vault passage.")
    return UpdateDetection()
