from __future__ import annotations

from app.graph import KnowledgeGraph


def test_sample_vault_graph_has_edges(graph: KnowledgeGraph):
    assert graph.graph.number_of_nodes() >= 3
    assert graph.graph.number_of_edges() >= 1
    related = graph.related_note_paths(["Python Basics"])
    assert related
    assert graph.resolve_wikilink("Data Structures") == "Data Structures.md"
