from __future__ import annotations

from pathlib import Path

from app.source_identity import upload_source_key
from app.sources.base import LoadedSource
from app.sources.docx import DocxLoader
from app.sources.epub import EpubLoader
from app.sources.pdf import PdfLoader
from app.sources.text import TextLoader
from app.sources.web import WebArticleLoader
from app.sources.youtube import YouTubeLoader
from app.url_security import validate_public_url


class SourceDispatcher:
    def __init__(self):
        self.path_loaders = [TextLoader(), PdfLoader(), EpubLoader(), DocxLoader()]
        # Order matters: YouTube first, then the generic web-article fallback.
        self.url_loaders = [YouTubeLoader(), WebArticleLoader()]

    def load_from_path(self, path: Path) -> LoadedSource:
        path = path.resolve()
        for loader in self.path_loaders:
            if loader.supports_path(path):
                return loader.load_from_path(path)
        raise ValueError(f"Unsupported file type: {path.suffix}")

    def load_from_url(self, url: str) -> LoadedSource:
        url = validate_public_url(url)
        for loader in self.url_loaders:
            if loader.supports_url(url):
                return loader.load_from_url(url)
        raise ValueError(f"Unsupported URL: {url}")

    def load_from_bytes(self, filename: str, content: bytes) -> LoadedSource:
        import tempfile

        suffix = Path(filename).suffix.lower()
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(content)
            tmp_path = Path(tmp.name)

        try:
            loaded = self.load_from_path(tmp_path)
            # Preserve the parsed segments (with page/line locations); only swap
            # the temp-file identity for the original upload name.
            loaded.title = Path(filename).stem
            loaded.source_ref = filename
            loaded.source_key = upload_source_key(filename, content)
            return loaded
        finally:
            tmp_path.unlink(missing_ok=True)
