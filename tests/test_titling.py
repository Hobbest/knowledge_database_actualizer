"""Unit goldens for body-grounded concept titling (Phase 0 — no wiring)."""

from __future__ import annotations

from app.titling import (
    TITLE_ALGORITHM_VERSION,
    concept_title_from_body,
    disambiguate_titles,
    refine_topic_title,
    title_body_coherence,
    title_is_grounded,
)


def _assert_concept_family(title: str, *needles: str) -> None:
    lowered = title.casefold()
    assert any(needle.casefold() in lowered for needle in needles), (
        f"Expected one of {needles!r} in title {title!r}"
    )
    assert "..." not in title
    assert not title.endswith((" and", " of", " the", " a"))


def test_title_algorithm_version_is_stable_string():
    assert isinstance(TITLE_ALGORITHM_VERSION, str)
    assert TITLE_ALGORITHM_VERSION


def test_definitional_sentence_yields_short_concept():
    body = (
        "Learning rate schedules are a family of rules that change the step size "
        "during training. Practitioners often use cosine decay with warm restarts "
        "to improve convergence on image models."
    )
    title = concept_title_from_body(body)
    _assert_concept_family(title, "Learning rate", "Learning Rate", "rate schedules")
    assert len(title.split()) <= 8


def test_misaligned_heading_is_discarded():
    body = (
        "Gradient clipping is a technique that limits the magnitude of gradients "
        "before the optimizer step. It stabilizes training when loss spikes."
    )
    title = concept_title_from_body(body, hint="Appendix A: Notation")
    assert "appendix" not in title.casefold()
    _assert_concept_family(title, "Gradient", "clipping")


def test_aligned_heading_is_kept():
    body = (
        "## Gradient Clipping\n\n"
        "Gradient clipping is a technique that limits the magnitude of gradients "
        "before the optimizer step. Large updates can destabilize recurrent nets."
    )
    title = concept_title_from_body(body, hint="Gradient Clipping")
    _assert_concept_family(title, "Gradient Clipping", "Gradient clipping")


def test_choppy_first_line_loses_to_body_grounded_candidate():
    # Simulates a hard-wrapped PDF lead-in that title_from_text would keep.
    body = (
        "When the step size is too lar\n"
        "ge, optimization oscillates and fails to converge. Learning rate "
        "warmup gradually increases the step size for the first epochs so "
        "early batches do not dominate the trajectory."
    )
    title = refine_topic_title(body)
    assert "too lar" not in title.casefold()
    _assert_concept_family(title, "Learning rate", "warmup", "step size", "Optimization")


def test_coherence_gate_prefers_grounded_fallback_for_weak_hint():
    body = (
        "Batch normalization renormalizes activations using mini-batch statistics. "
        "It reduces internal covariate shift and allows higher learning rates."
    )
    title = refine_topic_title(body, hint="See figure")
    assert "see figure" not in title.casefold()
    _assert_concept_family(title, "Batch", "normalization", "covariate")


def test_title_body_coherence_scores_overlap():
    body = "Cosine similarity measures the angle between embedding vectors."
    assert title_body_coherence("Cosine similarity", body) > 0.2
    assert title_body_coherence("Unrelated astronomy facts", body) < 0.15


def test_title_is_grounded_uses_asymmetric_coverage():
    long_body = (
        "A precise definition of the concept for the vault. "
        "Additional details expand on usage, trade-offs, and related ideas."
    )
    assert title_is_grounded("Concept", long_body)
    assert not title_is_grounded("Momentum Accumulation", long_body)


def test_disambiguate_titles_only_suffixes_collisions():
    titles = ["Alpha Concept", "Beta Concept", "Alpha Concept", "Gamma"]
    result = disambiguate_titles(titles)
    assert result[1] == "Beta Concept"
    assert result[3] == "Gamma"
    assert result[0].startswith("Alpha Concept")
    assert result[2].startswith("Alpha Concept")
    assert result[0] != result[2]
    assert "(part" in result[0].casefold() or "(part" in result[2].casefold()


def test_disambiguate_unique_titles_unchanged():
    assert disambiguate_titles(["One", "Two", "Three"]) == ["One", "Two", "Three"]


def test_refine_empty_body_uses_hint_or_untitled():
    assert refine_topic_title("", hint="Solid Hint Title") == "Solid Hint Title"
    assert refine_topic_title("") == "Untitled concept"
