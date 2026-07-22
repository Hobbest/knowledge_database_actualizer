from __future__ import annotations

import logging
import secrets
from time import perf_counter

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api import (
    admin_router,
    chat_router,
    sources_router,
    suggestions_router,
    vault_router,
)
from app.api.common import (
    _STREAM_DONE,  # noqa: F401
    _iter_analyze_events,  # noqa: F401
    _ndjson_worker,  # noqa: F401
    _queue_to_async_stream,  # noqa: F401
    _read_upload_bounded,  # noqa: F401
    _refresh_written_notes,  # noqa: F401
    _resolve_vault_path,  # noqa: F401
    _run_full_index,
)
from app.auth import required_capabilities, token_grants_capabilities
from app.config import settings
from app.deps import (
    FRONTEND_DIR,
    SHARED_DIR,
    get_vector_store,  # noqa: F401
    graph,  # noqa: F401
    source_dispatcher,  # noqa: F401
    vector_store,  # noqa: F401
)
from app.embeddings import chunk_size_error
from app.novelty import analyze_source_similarity  # noqa: F401
from app.observability import configure_logging, metrics
from app.settings_persistence import update_env_values  # noqa: F401
from app.vault_watcher import vault_watch

# Re-export for tests that monkeypatch symbols on the composition root.
__all_test_hooks__ = (
    "analyze_source_similarity",
    "update_env_values",
    "vault_watch",
)

logger = logging.getLogger(__name__)

# Refuse to start with a chunk size the embedding model cannot actually embed;
# novelty verdicts would silently be based on truncated text.
_chunk_error = chunk_size_error()
if _chunk_error:
    raise RuntimeError(_chunk_error)

# Refuse public binds without a token (Docker / 0.0.0.0).
settings.require_api_token_for_bind_host()
settings.require_networked_profile()

# The SPA is served same-origin from this app, so no CORS middleware exists on
# purpose: browsers then refuse cross-origin reads and preflighted requests from
# other websites. The Host allowlist below additionally blocks DNS-rebinding
# attacks, where an attacker's hostname resolves to 127.0.0.1 to bypass the
# browser's same-origin policy against a local server.
app = FastAPI(title="Knowledge Database Actualizer", version="0.1.0")

app.include_router(admin_router)
app.include_router(vault_router)
app.include_router(sources_router)
app.include_router(suggestions_router)
app.include_router(chat_router)


def _host_allowed(host_header: str) -> bool:
    host = host_header.strip().lower()
    if host.startswith("["):  # IPv6 literal, e.g. [::1]:8000
        host = host.split("]", 1)[0] + "]"
    else:
        host = host.split(":", 1)[0]
    return host in settings.allowed_host_set


@app.middleware("http")
async def request_observability(request, call_next):
    started = perf_counter()
    status_code = 500
    try:
        response = await call_next(request)
        status_code = response.status_code
        return response
    finally:
        duration_ms = (perf_counter() - started) * 1000
        metrics.record_request(duration_ms, error=status_code >= 400)
        logger.info(
            "request method=%s path=%s status=%d duration_ms=%.1f",
            request.method,
            request.url.path,
            status_code,
            duration_ms,
        )


@app.middleware("http")
async def host_allowlist(request, call_next):
    """Reject requests whose Host is not in ALLOWED_HOSTS (DNS-rebinding guard)."""
    if not settings.allowed_host_set:  # empty = check disabled
        return await call_next(request)
    if _host_allowed(request.headers.get("host", "")):
        return await call_next(request)
    return JSONResponse(
        status_code=400,
        content={
            "detail": "Invalid Host header. Add the host to ALLOWED_HOSTS "
            "if you are serving beyond localhost."
        },
    )


def _token_matches(provided: str, expected: str) -> bool:
    # Constant-time comparison; bytes form also tolerates non-ASCII tokens.
    return secrets.compare_digest(provided.encode("utf-8"), expected.encode("utf-8"))


@app.middleware("http")
async def api_token_auth(request, call_next):
    """When API_TOKEN is set, require it and route capabilities for /api/*."""
    token = settings.api_token
    if not token:
        return await call_next(request)

    path = request.url.path
    if path == "/" or path.startswith("/static"):
        return await call_next(request)
    if not path.startswith("/api/"):
        return await call_next(request)

    auth = request.headers.get("Authorization", "")
    bearer = auth.removeprefix("Bearer ") if auth.startswith("Bearer ") else ""
    header_token = request.headers.get("X-API-Token", "")
    if not (
        (bearer and _token_matches(bearer, token))
        or (header_token and _token_matches(header_token, token))
    ):
        return JSONResponse(status_code=401, content={"detail": "Unauthorized"})

    required = required_capabilities(request.method, path)
    granted = settings.api_token_capability_set
    if not token_grants_capabilities(granted, required):
        return JSONResponse(
            status_code=403,
            content={
                "detail": (
                    f"Token lacks required capabilities: "
                    f"{', '.join(sorted(required))}"
                )
            },
        )
    return await call_next(request)


@app.on_event("startup")
def startup_vault_watch() -> None:
    configure_logging(settings.log_level, settings.log_format)
    vault_watch.configure(_run_full_index)
    vault_watch.start()


@app.on_event("shutdown")
def shutdown_vault_watch() -> None:
    vault_watch.stop()


@app.get("/")
def serve_index():
    index_path = FRONTEND_DIR / "index.html"
    if not index_path.exists():
        raise HTTPException(status_code=404, detail="Frontend not found")
    return FileResponse(index_path)


if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")

if SHARED_DIR.exists():
    app.mount("/shared", StaticFiles(directory=SHARED_DIR), name="shared")


def run() -> None:
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=settings.bind_host,
        port=8000,
        reload=False,
    )


if __name__ == "__main__":
    run()
