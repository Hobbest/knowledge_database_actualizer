from __future__ import annotations

from dataclasses import dataclass

from app.llm import call_with_retry, get_llm_provider
from app.prompts import wrap_untrusted
from app.vector_protocol import VectorStoreProtocol as VectorStore
from app.vectorstore import SimilarChunk


@dataclass(frozen=True)
class ChatAnswer:
    answer: str
    citations: list[dict]


def answer_vault_question(
    question: str,
    store: VectorStore,
    *,
    source_context: str | None = None,
    top_k: int = 5,
) -> ChatAnswer:
    matches = store.query_similar(question, top_k=top_k)
    if not matches:
        raise ValueError("No indexed vault context is available for this question.")
    citations = [_citation(match, index + 1) for index, match in enumerate(matches)]
    context = "\n\n".join(
        f"[{index}] {match.note_path}"
        f"{' > ' + match.heading if match.heading else ''}\n{match.text}"
        for index, match in enumerate(matches, 1)
    )
    if source_context:
        context += "\n\nOptional current source:\n" + source_context[:4000]
    provider = get_llm_provider()
    if provider is None:
        answer = (
            "No LLM is configured. The most relevant vault passages are: "
            + "; ".join(f"[{i}] {item.note_title}" for i, item in enumerate(matches, 1))
            + "."
        )
    else:
        prompt = (
            f"Answer this question using only the supplied context: {question}\n\n"
            f"{wrap_untrusted('retrieved vault context', context)}\n\n"
            "Cite claims inline as [1], [2], etc. If context is insufficient, say so."
        )
        answer = call_with_retry(
            lambda: provider.complete(
                prompt,
                system="You answer questions from an Obsidian vault with precise citations.",
            )
        ).strip()
    return ChatAnswer(answer=answer, citations=citations)


def _citation(match: SimilarChunk, index: int) -> dict:
    return {
        "id": index,
        "note_path": match.note_path,
        "note_title": match.note_title,
        "heading": match.heading,
        "score": round(match.similarity, 3),
        "snippet": match.text[:500],
    }
