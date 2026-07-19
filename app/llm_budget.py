"""Per-run LLM call / input-size budget to cap cost on large sources."""

from __future__ import annotations

from dataclasses import dataclass, field

from app.config import settings


@dataclass
class LLMBudget:
    """Tracks LLM usage for a single analyze/draft run.

    ``max_calls`` counts every ``complete()`` (topic planning + note drafts).
    ``max_input_chars`` is a rough spend proxy (≈ tokens × 4 for English).
    """

    max_calls: int
    max_input_chars: int
    calls: int = 0
    input_chars: int = 0
    exhausted_reason: str | None = None
    _warnings: list[str] = field(default_factory=list)

    @classmethod
    def from_settings(cls) -> LLMBudget:
        return cls(
            max_calls=settings.llm_max_calls_per_run,
            max_input_chars=settings.llm_max_input_chars_per_run,
        )

    @property
    def remaining_calls(self) -> int:
        if self.max_calls <= 0:
            return 10**9
        return max(0, self.max_calls - self.calls)

    @property
    def remaining_chars(self) -> int:
        if self.max_input_chars <= 0:
            return 10**9
        return max(0, self.max_input_chars - self.input_chars)

    @property
    def exhausted(self) -> bool:
        return self.exhausted_reason is not None

    def can_call(self, prompt_chars: int) -> bool:
        if self.exhausted:
            return False
        if self.max_calls > 0 and self.calls >= self.max_calls:
            return False
        if self.max_input_chars > 0 and self.input_chars + prompt_chars > self.max_input_chars:
            return False
        return True

    def record(self, prompt_chars: int) -> None:
        self.calls += 1
        self.input_chars += max(0, prompt_chars)

    def refuse(self, prompt_chars: int) -> str:
        """Mark budget exhausted and return a user-facing reason."""
        if self.max_calls > 0 and self.calls >= self.max_calls:
            reason = (
                f"LLM call budget reached ({self.calls}/{self.max_calls} calls). "
                "Remaining notes use extractive summaries."
            )
        elif self.max_input_chars > 0 and self.input_chars + prompt_chars > self.max_input_chars:
            reason = (
                f"LLM input budget reached ({self.input_chars:,}/{self.max_input_chars:,} chars). "
                "Remaining notes use extractive summaries."
            )
        else:
            reason = "LLM budget exhausted; remaining notes use extractive summaries."
        self.exhausted_reason = reason
        if reason not in self._warnings:
            self._warnings.append(reason)
        return reason

    def summary(self) -> dict:
        return {
            "calls": self.calls,
            "max_calls": self.max_calls,
            "input_chars": self.input_chars,
            "max_input_chars": self.max_input_chars,
            "exhausted": self.exhausted,
            "estimated_input_tokens": self.input_chars // 4,
        }

    @property
    def warnings(self) -> list[str]:
        return list(self._warnings)


def estimate_run_cost_hint(*, note_count: int, planning: bool) -> str:
    """Short human hint shown before/after drafting (not a billing quote)."""
    batch = max(1, settings.llm_draft_batch_size)
    draft_calls = (note_count + batch - 1) // batch
    calls = draft_calls + (1 if planning else 0)
    # Very rough free-tier-friendly guidance; not provider-accurate pricing.
    budget_calls = settings.llm_max_calls_per_run
    budget_chars = settings.llm_max_input_chars_per_run
    budget_label = (
        "unlimited"
        if budget_calls == 0 and budget_chars == 0
        else f"{budget_calls} calls / {budget_chars:,} input chars"
    )
    return f"Up to ~{calls} LLM call(s) for this run (budget: {budget_label})."
