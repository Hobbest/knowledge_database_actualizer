"""Unit tests for progressive EvidencePack packing (Phase 0 — no draft wire)."""

from __future__ import annotations

from app.progressive import (
    EVIDENCE_PACK_VERSION,
    build_evidence_pack,
    format_for_prompt,
    pack_to_budget,
    render_progressive_note,
)
from app.text_limits import TEXT_LIMITS


def test_evidence_pack_version_is_set():
    assert isinstance(EVIDENCE_PACK_VERSION, str)
    assert EVIDENCE_PACK_VERSION


def test_late_salient_claim_beats_opening_filler():
    filler = " ".join(
        [
            "This chapter introduces background material for orientation only.",
            "Readers may skim these remarks without missing the core result.",
            "The preamble continues with generic commentary about the field.",
            "Historical anecdotes and soft motivation fill several sentences here.",
        ]
        * 3
    )
    late = (
        "AdaGrad is an adaptive gradient method that scales each parameter "
        "update by the inverse square root of accumulated squared gradients. "
        "However, its accumulation can cause the effective learning rate to "
        "decay too quickly on non-convex problems."
    )
    body = f"{filler} {late}"
    pack = build_evidence_pack(body)
    blob = " ".join([pack.l3_executive, *pack.l2_essentials, *pack.l1_salient]).casefold()
    assert "adagrad" in blob or "adaptive gradient" in blob
    assert "however" in blob or "decay" in blob or "learning rate" in blob
    # Opening filler should not be the only evidence.
    assert "anecdotes" not in blob or "adagrad" in blob


def test_pack_to_budget_respects_single_and_batch_caps():
    sentences = [
        f"Definition {i} is a precise claim about method {i} with numeric rate {i}.0 percent."
        for i in range(1, 20)
    ]
    body = " ".join(sentences)
    pack = build_evidence_pack(body)

    for limit in (
        TEXT_LIMITS.note_draft_excerpt_chars,
        TEXT_LIMITS.batch_draft_excerpt_chars,
        400,
        200,
    ):
        packed = pack_to_budget(pack, limit)
        assert len(format_for_prompt(packed)) <= limit


def test_grounded_planner_summary_kept_in_l3():
    body = (
        "Momentum accumulates a velocity vector from past gradients. "
        "It dampens oscillations and accelerates descent in consistent directions."
    )
    summary = "Momentum accumulates velocity from past gradients to dampen oscillations."
    pack = build_evidence_pack(body, planner_summary=summary)
    assert "momentum" in pack.l3_executive.casefold()
    assert "velocity" in pack.l3_executive.casefold()


def test_ungrounded_planner_summary_replaced():
    body = (
        "Batch normalization renormalizes layer activations using mini-batch statistics. "
        "It reduces internal covariate shift during deep network training."
    )
    pack = build_evidence_pack(
        body,
        planner_summary="Unrelated astronomy facts about distant galaxies and nebulae.",
    )
    assert "astronomy" not in pack.l3_executive.casefold()
    assert (
        "batch" in pack.l3_executive.casefold()
        or "normalization" in pack.l3_executive.casefold()
        or "activations" in pack.l3_executive.casefold()
    )


def test_render_progressive_note_has_blockquote_and_bold_nucleus():
    filler = " ".join(
        ["Generic preamble sentence number %d fills space without claims." % i for i in range(12)]
    )
    core = (
        "Cosine similarity is a measure of the angle between embedding vectors. "
        "It ignores magnitude and focuses on orientation. "
        "However, sparse vectors can make the score unstable without smoothing."
    )
    body = f"{filler} {core}"
    pack = build_evidence_pack(body)
    note = render_progressive_note("Cosine Similarity", pack)
    assert note.lstrip().startswith("# Cosine Similarity")
    assert ">" in note
    assert "**" in note
    assert "## Key points" in note
    # Progressive note compresses — does not embed the full L0 source.
    assert body not in note
    assert "preamble sentence number 0" not in note or "cosine" in note.casefold()
    assert len(note) < len(body)


def test_format_for_prompt_includes_layer_labels():
    pack = build_evidence_pack(
        "Gradient descent is an optimization algorithm that minimizes a loss."
    )
    formatted = format_for_prompt(pack)
    assert "Executive summary" in formatted
    assert "Essential claims" in formatted
    assert "Salient source passages" in formatted


def test_media_hints_optional_and_budget_droppable():
    pack = build_evidence_pack(
        "Adam is an adaptive optimizer combining momentum and RMSprop ideas.",
        media_hints=["Figure 1: Loss curves for Adam vs SGD."],
    )
    assert pack.media_hints
    assert "Media hints" in format_for_prompt(pack)
    tiny = pack_to_budget(pack, 180)
    assert len(format_for_prompt(tiny)) <= 180


def test_evidence_for_topic_includes_media_hints(monkeypatch):
    from app.atomic_notes import AtomicTopic
    from app.sources.base import LoadedSource, MediaItem, SourceLocation, SourceSegment
    from app.suggest import draft as draft_mod
    from app.suggest.draft import _evidence_for_topic
    from app.text_limits import TEXT_LIMITS

    monkeypatch.setattr(draft_mod.settings, "include_media", True)
    location = SourceLocation(page=1)
    topic = AtomicTopic(
        title="Adam",
        segments=[
            SourceSegment(
                text="Adam is an adaptive optimizer combining momentum and RMSprop.",
                location=location,
                index=0,
            )
        ],
        summary="Adam optimizer",
    )
    source = LoadedSource(
        title="Src",
        text="body",
        source_type="text",
        source_ref="a.txt",
        media=[
            MediaItem(
                kind="figure",
                label="Figure 1",
                caption="Loss curves for Adam vs SGD",
                location=location,
            )
        ],
    )
    evidence = _evidence_for_topic(
        topic,
        max_chars=TEXT_LIMITS.note_draft_excerpt_chars,
        source=source,
    )
    assert "Media hints" in evidence
    assert "Loss curves" in evidence or "Figure 1" in evidence


def test_draft_evidence_includes_late_salient_claim():
    from app.atomic_notes import AtomicTopic
    from app.sources.base import SourceLocation, SourceSegment
    from app.suggest.draft import _batch_draft_payload, _evidence_for_topic
    from app.text_limits import TEXT_LIMITS

    filler = " ".join(
        [
            "This chapter introduces background material for orientation only.",
            "Readers may skim these remarks without missing the core result.",
        ]
        * 4
    )
    late = (
        "AdaGrad is an adaptive gradient method that scales each parameter "
        "update by the inverse square root of accumulated squared gradients."
    )
    topic = AtomicTopic(
        title="AdaGrad",
        segments=[
            SourceSegment(
                text=f"{filler} {late}",
                location=SourceLocation(page=1),
                index=0,
            )
        ],
        summary="Background orientation remarks.",
    )
    evidence = _evidence_for_topic(
        topic,
        max_chars=TEXT_LIMITS.note_draft_excerpt_chars,
    )
    assert "adagrad" in evidence.casefold() or "adaptive gradient" in evidence.casefold()
    assert "Executive summary" in evidence

    batch = _batch_draft_payload([topic])
    assert len(batch) == 1
    assert "evidence" in batch[0]
    assert "excerpt" not in batch[0]
    assert len(batch[0]["evidence"]) <= TEXT_LIMITS.batch_draft_excerpt_chars
    assert "adagrad" in batch[0]["evidence"].casefold() or "adaptive" in batch[0]["evidence"].casefold()


def test_batch_draft_payload_respects_batch_budget():
    from app.atomic_notes import AtomicTopic
    from app.sources.base import SourceLocation, SourceSegment
    from app.suggest.draft import _batch_draft_payload
    from app.text_limits import TEXT_LIMITS

    text = " ".join(
        f"Claim {i} is a precise statement about method {i} with rate {i}.5 percent."
        for i in range(30)
    )
    topics = [
        AtomicTopic(
            title=f"Topic {i}",
            segments=[
                SourceSegment(text=text, location=SourceLocation(page=1), index=i)
            ],
            summary=f"Topic {i} overview",
        )
        for i in range(3)
    ]
    payload = _batch_draft_payload(topics)
    assert len(payload) == 3
    for item in payload:
        assert len(item["evidence"]) <= TEXT_LIMITS.batch_draft_excerpt_chars
        assert "summary" not in item
        assert "excerpt" not in item


def test_atomic_note_rules_describe_progressive_shape():
    from app.prompts import ATOMIC_NOTE_RULES

    joined = " ".join(ATOMIC_NOTE_RULES).casefold()
    assert "blockquote" in joined or ">" in joined or "executive" in joined
    assert "key points" in joined
    assert "nucleus" in joined or "bold" in joined
    assert "paraphrase" in joined


def test_fallback_topic_body_progressive_shape():
    from app.atomic_notes import AtomicTopic
    from app.sources.base import SourceLocation, SourceSegment
    from app.suggest.draft import _fallback_topic_body

    topic = AtomicTopic(
        title="Cosine Similarity",
        segments=[
            SourceSegment(
                text=(
                    "Cosine similarity is a measure of the angle between embedding vectors. "
                    "It ignores magnitude and focuses on orientation. "
                    "However, sparse vectors can make the score unstable without smoothing."
                ),
                location=SourceLocation(page=1),
                index=0,
            )
        ],
        summary="Cosine similarity measures embedding orientation.",
    )
    body = _fallback_topic_body(topic)
    assert body.lstrip().startswith("# Cosine Similarity")
    assert ">" in body
    assert "**" in body
    assert "## Key points" in body
    assert "## Summary" not in body


def test_fallback_progressive_note_quality_spot_check():
    from app.atomic_notes import AtomicTopic
    from app.note_intelligence import score_note_quality
    from app.sources.base import LoadedSource, SourceLocation, SourceSegment
    from app.suggest.draft import _build_suggestion, _fallback_topic_body

    topic = AtomicTopic(
        title="Momentum",
        segments=[
            SourceSegment(
                text=(
                    "Momentum accumulates a velocity vector from past gradients. "
                    "It dampens oscillations and accelerates descent in consistent directions. "
                    "However, a high momentum coefficient can overshoot sharp minima."
                ),
                location=SourceLocation(page=1),
                index=0,
            )
        ],
        summary="Momentum accumulates velocity from past gradients.",
    )
    body = _fallback_topic_body(topic)
    # Mimic drafted note structure after frontmatter would be added in apply —
    # quality scoring looks at concept_title vs body prose.
    wrapped = (
        "---\ntype: atomic\n---\n"
        f"{body}"
        "## Related notes\n\n- [[topics/Other]]\n\n"
        "## Source\n\n- File: `a.md`\n"
    )
    quality = score_note_quality(concept_title="Momentum", content=wrapped)
    assert "heading_mismatch" not in quality["quality_flags"]
    assert "title_ungrounded" not in quality["quality_flags"]

    source = LoadedSource(
        title="Src",
        text="Momentum text",
        source_type="text",
        source_ref="a.txt",
    )
    suggestion = _build_suggestion(
        source,
        topic,
        [],
        set(),
        use_llm=False,
        vector_store=None,
    )
    assert suggestion.concept_title == "Momentum"
    assert "# Momentum" in suggestion.content
    assert ">" in suggestion.content
    assert "**" in suggestion.content


def test_should_llm_deep_read_gates(monkeypatch):
    from app.atomic_notes import AtomicTopic
    from app.llm_budget import LLMBudget
    from app.sources.base import SourceLocation, SourceSegment
    from app.suggest.draft import _should_llm_deep_read

    topic = AtomicTopic(
        title="Novel",
        segments=[
            SourceSegment(text="Novel concept body text.", location=SourceLocation(), index=0)
        ],
        is_novel=True,
    )
    known = AtomicTopic(
        title="Known",
        segments=[
            SourceSegment(text="Known concept body text.", location=SourceLocation(), index=0)
        ],
        is_novel=False,
    )

    monkeypatch.setattr("app.suggest.draft.settings.draft_llm_deep_read", False)
    monkeypatch.setattr("app.suggest.draft.settings.llm_draft_batch_size", 1)
    assert not _should_llm_deep_read(topic, budget=None)

    monkeypatch.setattr("app.suggest.draft.settings.draft_llm_deep_read", True)
    assert _should_llm_deep_read(topic, budget=None)
    assert not _should_llm_deep_read(known, budget=None)

    monkeypatch.setattr("app.suggest.draft.settings.llm_draft_batch_size", 3)
    assert not _should_llm_deep_read(topic, budget=None)

    monkeypatch.setattr("app.suggest.draft.settings.llm_draft_batch_size", 1)
    tight = LLMBudget(max_calls=2, max_input_chars=0)
    tight.calls = 1  # only 1 remaining — not enough for claims + synthesize
    assert not _should_llm_deep_read(topic, budget=tight)
    roomy = LLMBudget(max_calls=5, max_input_chars=0)
    assert _should_llm_deep_read(topic, budget=roomy)


def test_parse_deep_read_claims_accepts_object():
    from app.suggest.draft import _format_claims_block, _parse_deep_read_claims

    parsed = _parse_deep_read_claims(
        '```json\n{"claims": ["AdaGrad adapts per-parameter rates"], '
        '"terms": ["AdaGrad"], "caveats": ["Can decay too fast"]}\n```'
    )
    assert parsed is not None
    assert "AdaGrad" in parsed["terms"]
    block = _format_claims_block(parsed)
    assert "Claims:" in block
    assert "Caveats:" in block


def test_llm_draft_runs_deep_read_when_enabled(monkeypatch):
    from app.atomic_notes import AtomicTopic
    from app.llm_budget import LLMBudget
    from app.sources.base import LoadedSource, SourceLocation, SourceSegment
    from app.suggest.draft import _llm_draft_topic_body

    monkeypatch.setattr("app.suggest.draft.settings.draft_llm_deep_read", True)
    monkeypatch.setattr("app.suggest.draft.settings.llm_draft_batch_size", 1)

    calls: list[str] = []

    class Provider:
        def complete(self, prompt, *, system=None, json_mode=False):
            calls.append(system or "")
            if system and "extract factual claims" in system.casefold():
                return (
                    '{"claims": ["Momentum accumulates velocity"], '
                    '"terms": ["momentum"], "caveats": ["Can overshoot"]}'
                )
            assert "LLM deep-read extraction" in prompt
            assert "Momentum accumulates velocity" in prompt
            return "# Momentum\n\n> Momentum accumulates velocity.\n"

    monkeypatch.setattr("app.suggest.draft.get_llm_provider", lambda: Provider())

    topic = AtomicTopic(
        title="Momentum",
        segments=[
            SourceSegment(
                text=(
                    "Momentum accumulates a velocity vector from past gradients. "
                    "It dampens oscillations during steepest descent."
                ),
                location=SourceLocation(page=1),
                index=0,
            )
        ],
        summary="Momentum accumulates velocity from past gradients.",
        is_novel=True,
    )
    source = LoadedSource(
        title="Src",
        text="body",
        source_type="text",
        source_ref="a.txt",
    )
    budget = LLMBudget(max_calls=10, max_input_chars=0)
    body = _llm_draft_topic_body(source, topic, [], budget=budget)
    assert body is not None
    assert "Momentum" in body
    assert budget.calls == 2  # claims + synthesize
    assert any("extract factual claims" in item.casefold() for item in calls)


def test_llm_draft_skips_deep_read_when_batch_size_gt_one(monkeypatch):
    from app.atomic_notes import AtomicTopic
    from app.sources.base import LoadedSource, SourceLocation, SourceSegment
    from app.suggest.draft import _llm_draft_topic_body

    monkeypatch.setattr("app.suggest.draft.settings.draft_llm_deep_read", True)
    monkeypatch.setattr("app.suggest.draft.settings.llm_draft_batch_size", 3)

    calls = {"n": 0}

    class Provider:
        def complete(self, prompt, *, system=None, json_mode=False):
            calls["n"] += 1
            assert system is None or "extract factual claims" not in (system or "").casefold()
            return "# Topic\n\nBody.\n"

    monkeypatch.setattr("app.suggest.draft.get_llm_provider", lambda: Provider())
    topic = AtomicTopic(
        title="Topic",
        segments=[
            SourceSegment(
                text="Topic is a precise concept with enough explanatory detail.",
                location=SourceLocation(page=1),
                index=0,
            )
        ],
        is_novel=True,
    )
    source = LoadedSource(title="Src", text="body", source_type="text", source_ref="a.txt")
    body = _llm_draft_topic_body(source, topic, [], budget=None)
    assert body is not None
    assert calls["n"] == 1
