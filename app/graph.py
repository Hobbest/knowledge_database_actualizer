from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import networkx as nx

from app.vault import VaultIndexResult, VaultNote, load_note, load_vault
from app.wikilinks import WikilinkIndex, build_wikilink_index


class KnowledgeGraph:
    def __init__(self):
        self.graph = nx.DiGraph()
        self._link_index: WikilinkIndex = WikilinkIndex()

    def build_from_vault(self, vault_path: Path) -> VaultIndexResult:
        vault_result = load_vault(vault_path)
        self.graph.clear()
        self._link_index = build_wikilink_index(vault_result.notes)

        for note in vault_result.notes:
            self.graph.add_node(
                note.path.as_posix(),
                label=note.title,
                title=note.title,
                aliases=list(note.aliases),
            )

        for note in vault_result.notes:
            source = note.path.as_posix()
            for link in note.wikilinks:
                target = self._link_index.resolve(link)
                if target:
                    self.graph.add_edge(source, target, label=link)

        return vault_result

    def upsert_notes(self, vault_path: Path, relative_paths: list[str]) -> dict[str, Any]:
        """Add or replace nodes/edges for the given vault-relative note paths."""
        vault_path = vault_path.resolve()
        notes: list[VaultNote] = []
        missing: list[str] = []
        for rel in dict.fromkeys(p for p in relative_paths if p):
            note = load_note(vault_path, rel)
            if note is None:
                if rel in self.graph:
                    self.graph.remove_node(rel)
                missing.append(rel)
                continue
            notes.append(note)

        for note in notes:
            path = note.path.as_posix()
            if path in self.graph:
                self.graph.remove_node(path)
            self.graph.add_node(
                path,
                label=note.title,
                title=note.title,
                aliases=list(note.aliases),
            )

        # Rebuild the full link index from current graph nodes + new notes so
        # path-qualified and alias links resolve against the live vault view.
        # For upsert we only have partial notes; merge with existing node titles.
        existing_as_notes = [
            VaultNote(
                path=Path(node_id),
                title=data.get("title") or Path(node_id).stem,
                content="",
                frontmatter={},
                aliases=list(data.get("aliases") or []),
            )
            for node_id, data in self.graph.nodes(data=True)
        ]
        # Prefer freshly loaded note objects when present.
        by_path = {n.path.as_posix(): n for n in existing_as_notes}
        for note in notes:
            by_path[note.path.as_posix()] = note
        self._link_index = build_wikilink_index(list(by_path.values()))

        for note in notes:
            source = note.path.as_posix()
            for link in note.wikilinks:
                target = self._link_index.resolve(link)
                if target:
                    self.graph.add_edge(source, target, label=link)

        return {"updated_notes": len(notes), "missing_notes": missing}

    def to_vis_json(self, highlight_nodes: list[str] | None = None) -> dict[str, Any]:
        highlight = set(highlight_nodes or [])
        nodes = []
        edges = []

        for node_id, data in self.graph.nodes(data=True):
            nodes.append(
                {
                    "id": node_id,
                    "label": data.get("label", node_id),
                    "highlighted": node_id in highlight,
                }
            )

        for source, target, data in self.graph.edges(data=True):
            # The link text equals the destination note's title, so rendering it
            # directly on the edge just duplicates the target node label. Keep it as
            # a hover tooltip ("title") instead of a permanently visible "label".
            edges.append(
                {
                    "from": source,
                    "to": target,
                    "title": data.get("label", ""),
                }
            )

        return {"nodes": nodes, "edges": edges}

    def resolve_wikilink(self, link: str) -> str | None:
        """Resolve an Obsidian link target to a vault-relative note path."""
        return self._link_index.resolve(link)

    def related_note_paths(self, note_titles: list[str]) -> list[str]:
        """Highlight notes by title, alias, or vault path."""
        related: set[str] = set()
        for title in note_titles:
            node = self._link_index.resolve(title) if title else None
            if not node:
                # Fallback: match stored title attribute exactly.
                for node_id, data in self.graph.nodes(data=True):
                    if data.get("title") == title or node_id == title:
                        node = node_id
                        break
            if not node:
                continue
            related.add(node)
            related.update(self.graph.predecessors(node))
            related.update(self.graph.successors(node))
        return sorted(related)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = nx.node_link_data(self.graph, edges="links")
        path.write_text(json.dumps(data), encoding="utf-8")

    def load(self, path: Path) -> bool:
        if not path.exists():
            return False
        data = json.loads(path.read_text(encoding="utf-8"))
        self.graph = nx.node_link_graph(data, directed=True, edges="links")
        # Rebuild link index from loaded nodes so resolve/related keep working.
        notes = [
            VaultNote(
                path=Path(node_id),
                title=data.get("title") or Path(node_id).stem,
                content="",
                frontmatter={},
                aliases=list(data.get("aliases") or []),
            )
            for node_id, data in self.graph.nodes(data=True)
        ]
        self._link_index = build_wikilink_index(notes)
        return True
