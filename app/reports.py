from __future__ import annotations

import html
import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict, is_dataclass
from typing import Any


def _mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    return {}


def _display(value: Any, default: str = "Not provided") -> str:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, float):
        return f"{value:.3f}".rstrip("0").rstrip(".")
    if isinstance(value, (Mapping, list, tuple)):
        return json.dumps(value, ensure_ascii=False, default=str)
    return str(value)


def _items(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return list(value)
    return [value]


def _report_sections(data: Mapping[str, Any]) -> list[tuple[str, list[tuple[str, str]]]]:
    root = dict(data)
    analysis = _mapping(root.get("analysis") or root.get("result"))
    source = _mapping(root.get("source") or analysis.get("source"))
    novelty = _mapping(
        root.get("novelty")
        or analysis.get("novelty")
        or root.get("novelty_result")
    )

    source_rows = [
        ("Title", _display(source.get("title") or root.get("source_title"))),
        ("Type", _display(source.get("source_type") or source.get("type"))),
        ("Reference", _display(source.get("source_ref") or source.get("url") or source.get("path"))),
    ]
    novelty_rows = [
        ("Verdict", _display(novelty.get("verdict") or root.get("verdict"))),
        (
            "Novelty score",
            _display(novelty.get("novelty_score", root.get("novelty_score"))),
        ),
        ("Novel chunks", _display(novelty.get("novel_chunks"))),
        ("Known chunks", _display(novelty.get("known_chunks"))),
    ]

    overlaps = (
        novelty.get("overlapping_notes")
        or analysis.get("overlapping_notes")
        or root.get("overlaps")
        or root.get("overlapping_notes")
    )
    overlap_rows: list[tuple[str, str]] = []
    for index, overlap in enumerate(_items(overlaps), start=1):
        item = _mapping(overlap)
        if item:
            label = _display(item.get("note_title") or item.get("note_path"), f"Overlap {index}")
            detail = _display(
                {
                    key: value
                    for key, value in item.items()
                    if key != "note_title" and value is not None and value != ""
                }
            )
            overlap_rows.append((label, detail))
        else:
            overlap_rows.append((f"Overlap {index}", _display(overlap)))

    suggestions = root.get("suggestions", analysis.get("suggestions"))
    suggestion_rows: list[tuple[str, str]] = []
    for index, suggestion in enumerate(_items(suggestions), start=1):
        item = _mapping(suggestion)
        label = _display(
            item.get("concept_title") or item.get("title") or item.get("name")
            if item
            else None,
            f"Suggestion {index}",
        )
        suggestion_rows.append((label, _display(item or suggestion)))
    quality = (
        root.get("suggestion_quality")
        or root.get("quality")
        or analysis.get("suggestion_quality")
    )
    duplicates = (
        root.get("duplicate_suggestions")
        or root.get("duplicates")
        or analysis.get("duplicate_suggestions")
    )
    suggestion_rows.extend(
        [
            ("Quality", _display(quality)),
            ("Duplicates", _display(duplicates, "None reported")),
        ]
    )

    return [
        ("Source", source_rows),
        ("Novelty", novelty_rows),
        ("Overlaps", overlap_rows or [("Summary", "None reported")]),
        ("Suggestions", suggestion_rows),
    ]


def generate_markdown_report(data: Mapping[str, Any], *, title: str = "Actualizer Report") -> str:
    """Render an analysis/result dictionary as portable Markdown."""

    lines = [f"# {title}", ""]
    for heading, rows in _report_sections(data):
        lines.extend((f"## {heading}", ""))
        for label, value in rows:
            safe_value = value.replace("\r", "").replace("\n", "  \n")
            lines.append(f"- **{label}:** {safe_value}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def generate_html_report(data: Mapping[str, Any], *, title: str = "Actualizer Report") -> str:
    """Render a standalone HTML report with all supplied content escaped."""

    safe_title = html.escape(title, quote=True)
    sections: list[str] = []
    for heading, rows in _report_sections(data):
        entries = "".join(
            "<dt>"
            + html.escape(label, quote=True)
            + "</dt><dd>"
            + html.escape(value, quote=True).replace("\n", "<br>")
            + "</dd>"
            for label, value in rows
        )
        sections.append(
            f"<section><h2>{html.escape(heading, quote=True)}</h2><dl>{entries}</dl></section>"
        )
    return (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{safe_title}</title>"
        "<style>body{font:16px/1.5 system-ui,sans-serif;max-width:960px;margin:2rem auto;"
        "padding:0 1rem;color:#202124}section{border-top:1px solid #ddd}"
        "dt{font-weight:700;margin-top:.75rem}dd{margin:.2rem 0 .75rem}</style>"
        f"</head><body><h1>{safe_title}</h1>{''.join(sections)}</body></html>"
    )


render_markdown_report = generate_markdown_report
render_html_report = generate_html_report
