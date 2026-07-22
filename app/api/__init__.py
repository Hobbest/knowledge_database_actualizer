from app.api.admin import router as admin_router
from app.api.chat import router as chat_router
from app.api.sources import router as sources_router
from app.api.suggestions import router as suggestions_router
from app.api.vault import router as vault_router

__all__ = [
    "admin_router",
    "chat_router",
    "sources_router",
    "suggestions_router",
    "vault_router",
]
