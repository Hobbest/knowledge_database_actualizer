from __future__ import annotations

from fastapi import APIRouter, Query
from fastapi.responses import Response

from app.analytics import load_analytics
from app.api.schemas import ReportExportRequest
from app.checkpoint import list_incomplete_checkpoints
from app.config import settings
from app.deps import get_vector_store, graph
from app.index_meta import (
    active_vault_path,
    collect_index_warnings,
    load_index_meta,
    stale_note_count,
)
from app.observability import metrics, recent_logs
from app.obsidian_uri import obsidian_uri_available
from app.plugin_api import plugin_status_summary
from app.reports import generate_html_report, generate_markdown_report
from app.runtime import INDEX_LOCK
from app.thresholds import recommended_thresholds_for, threshold_mismatch_warnings
from app.vault_watcher import vault_watch

router = APIRouter()


@router.get("/api/status")
def get_status():
    resolved_vault = active_vault_path()
    active_store = get_vector_store(resolved_vault)
    with INDEX_LOCK:
        indexed_chunks = active_store.chunk_count()
        graph_nodes = graph.graph.number_of_nodes()
        graph_edges = graph.graph.number_of_edges()
        if graph_nodes == 0 and settings.graph_cache_path.exists():
            graph.load(settings.graph_cache_path)
            graph_nodes = graph.graph.number_of_nodes()
            graph_edges = graph.graph.number_of_edges()

    vault_path = str(resolved_vault) if resolved_vault else None
    stale = stale_note_count(resolved_vault)
    warnings = collect_index_warnings(
        indexed_chunks=indexed_chunks,
        vault_path=resolved_vault,
    )
    warnings.extend(threshold_mismatch_warnings())
    return {
        "vault_path": vault_path,
        "indexed_chunks": indexed_chunks,
        "stale_note_count": stale,
        "graph_nodes": graph_nodes,
        "graph_edges": graph_edges,
        "graph_loaded": graph_nodes > 0,
        "llm_enabled": settings.llm_enabled,
        "llm_provider": settings.llm_provider,
        "auth_required": bool(settings.api_token),
        "auth": {
            "required": bool(settings.api_token),
            "capabilities_granted": sorted(settings.api_token_capability_set),
            "networked_profile": settings.is_networked_profile,
            "plugin_discovery_disabled": settings.disable_plugin_discovery,
            "plugin_allowlist": sorted(settings.plugin_allowlist_set),
        },
        "plugins": {
            "groups": plugin_status_summary(
                allowlist=settings.plugin_allowlist_set or None,
                disabled=settings.disable_plugin_discovery,
            )
        },
        "embedding_provider": settings.embedding_provider,
        "embedding_model": settings.embedding_model,
        "embedding_backend": settings.embedding_backend,
        "embedding_device": settings.embedding_device,
        "vector_backend": settings.vector_backend,
        "thresholds": {
            "novel": settings.novel_threshold,
            "known": settings.known_threshold,
            "recommended": recommended_thresholds_for(),
            "calibration_available": indexed_chunks >= 10,
        },
        "llm_budget": {
            "max_calls_per_run": settings.llm_max_calls_per_run,
            "max_input_chars_per_run": settings.llm_max_input_chars_per_run,
        },
        "index_meta": load_index_meta(),
        "incomplete_checkpoints": list_incomplete_checkpoints(),
        "warnings": warnings,
        "obsidian_vault_name": settings.obsidian_vault_name,
        "obsidian_uri_enabled": obsidian_uri_available(),
        "vault_watch": vault_watch.status(),
        "note_output": {
            "folder": settings.note_output_folder,
            "pattern": settings.note_output_pattern,
            "layout": settings.note_output_layout,
        },
        "analyze_in_place_enabled": settings.analyze_in_place_enabled,
        "llm_draft_batch_size": settings.llm_draft_batch_size,
        "multi_vault_index_enabled": settings.multi_vault_index_enabled,
        "append_under_overlap_heading": settings.append_under_overlap_heading,
        "git_auto_commit_on_apply": settings.git_auto_commit_on_apply,
        "intelligence": {
            "draft_rag_enabled": settings.draft_rag_enabled,
            "draft_rag_top_k": settings.draft_rag_top_k,
            "duplicate_detection_enabled": settings.duplicate_detection_enabled,
            "duplicate_similarity_threshold": settings.duplicate_similarity_threshold,
            "note_quality_scoring_enabled": settings.note_quality_scoring_enabled,
            "draft_llm_deep_read": settings.draft_llm_deep_read,
            "auto_tagging_enabled": settings.auto_tagging_enabled,
            "prompt_domain": settings.prompt_domain or None,
        },
        "capabilities": {
            "read": True,
            "analyze": True,
            "write": True,
            "admin": True,
            "chat": True,
            "audio_video": True,
            "vision": settings.vision_media_enabled,
            "rag_chat": True,
            "analytics": True,
        },
        "metrics": metrics.snapshot(),
    }


@router.get("/api/debug/recent-logs")
def get_recent_logs(limit: int = Query(default=100, ge=1, le=200)):
    """Return a bounded, redacted in-memory log view for local administration."""
    return {"logs": recent_logs(limit)}


@router.get("/api/analytics")
def get_analytics():
    return load_analytics()


@router.post("/api/reports/export")
def export_analysis_report(request: ReportExportRequest):
    """Download a portable report for the reviewed analysis result."""
    if request.format == "html":
        content = generate_html_report(request.result, title=request.title)
        media_type = "text/html"
        filename = "actualizer-report.html"
    else:
        content = generate_markdown_report(request.result, title=request.title)
        media_type = "text/markdown"
        filename = "actualizer-report.md"
    return Response(
        content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
