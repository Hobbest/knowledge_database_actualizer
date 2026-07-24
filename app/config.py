from pathlib import Path

from pydantic import BaseModel, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.auth import parse_api_token_capabilities


class EmbeddingsSettings(BaseModel):
    provider: str
    model: str
    device: str
    backend: str
    quantized: bool
    api_key: str | None


class VectorSettings(BaseModel):
    backend: str
    qdrant_url: str | None
    qdrant_api_key: str | None
    qdrant_collection: str
    qdrant_vector_size: int


class AuthSettings(BaseModel):
    api_token: str | None
    capabilities: frozenset[str]
    bind_host: str
    allowed_hosts: str


class PluginSettings(BaseModel):
    disable_discovery: bool
    allowlist: frozenset[str]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    vault_path: Path | None = None
    # Comma-separated absolute roots. When set, request vault_path must resolve
    # to a directory equal to or under one of these roots. When empty and
    # VAULT_PATH is set, only that exact vault is allowed. When both empty,
    # any existing directory is accepted (local first-run / tests).
    allowed_vault_roots: str = ""
    data_dir: Path = Path("./data")
    log_level: str = "INFO"
    log_format: str = "plain"  # plain | json

    # Embeddings: provider for similarity search (separate from LLM note drafting).
    # Defaults match .env.example — override via .env for Gemini or other models.
    # After changing defaults here, run: python scripts/env_sync.py generate
    embedding_provider: str = "local"  # local | gemini
    embedding_model: str = "all-MiniLM-L6-v2"  # local: HF model; gemini: e.g. gemini-embedding-001
    embedding_api_key: str | None = None  # optional; falls back to llm_api_key for gemini
    embedding_device: str = "auto"  # auto | cpu | cuda | mps
    embedding_backend: str = "sentence_transformers"  # sentence_transformers | onnx
    embedding_quantized: bool = False
    vector_backend: str = "chroma"  # chroma | qdrant
    qdrant_url: str | None = "http://127.0.0.1:6333"
    qdrant_api_key: str | None = None
    qdrant_collection: str = "actualizer_vault_chunks"
    qdrant_vector_size: int = 384

    novel_threshold: float = 0.55
    known_threshold: float = 0.75

    # Batch size for multi-query similarity (segment scoring / novelty).
    embedding_query_batch_size: int = 32
    # How many chunks to embed+add at a time during full vault indexing.
    chroma_index_batch_size: int = 64

    # Chunking for vault indexing + novelty comparison, in characters. Chunks
    # must fit the embedding model's input window or the tail of every chunk is
    # silently ignored when similarity is computed: all-MiniLM-L6-v2 truncates
    # at 256 tokens (~1000 chars). See max_embedding_input_chars() in
    # app/embeddings.py; oversized values raise at startup.
    chunk_size: int = 1000
    chunk_overlap: int = 150

    max_note_lines: int = 80
    max_notes_per_source: int = 1000
    atomic_note_line_limit: int = 40
    atomic_note_char_limit: int = 1800

    # Planning segmentation: large source units (e.g. full PDF pages) are split
    # into planning units of about this many characters before note planning, so
    # every distinct concept can become its own atomic note. Lower = more notes.
    segment_target_chars: int = 1200

    # Reject uploads larger than this many megabytes (0 = unlimited).
    max_upload_mb: int = 50
    # Cap outbound web/HTML fetch bodies (0 = unlimited). Separate from uploads.
    max_fetch_mb: int = 10

    # Comma-separated BCP-47 language codes for YouTube transcripts (tried in
    # order). When none match, the loader falls back to the first available
    # transcript language.
    youtube_transcript_languages: str = "en,en-US,en-GB"
    # Optional faster-whisper settings for uploaded audio/video.
    whisper_model: str = "small"
    whisper_device: str = "auto"
    whisper_compute_type: str = "default"
    whisper_language: str | None = None
    whisper_beam_size: int = 5

    # Skip non-substantive sections (acknowledgements, tables of contents,
    # reference lists, chapter summaries, contact/copyright pages) when planning.
    filter_boilerplate: bool = True

    # Detect tables (real PDF tables via pdfplumber + markdown pipe tables) and
    # figure captions, and attach the ones on a note's page/lines to that note.
    include_media: bool = True
    # Best-effort descriptions for image bytes made available by source loaders.
    vision_media_enabled: bool = False
    vision_model: str | None = None
    # Optional scanned-PDF OCR (install requirements-ocr.txt first).
    pdf_ocr_enabled: bool = False
    pdf_ocr_language: str = "eng"
    pdf_ocr_dpi: int = 200

    # Comma-separated tags added to every drafted note (Obsidian frontmatter).
    default_note_tags: str = "source-import"
    auto_tagging_enabled: bool = True
    auto_tagging_top_k: int = 3
    auto_tagging_max_tags: int = 8

    # Optional bundled prompt specialization: research | programming | history.
    prompt_domain: str = "programming"

    # Vault-relative folder for new notes (e.g. sources → sources/<source>/<concept>.md).
    note_output_folder: str = "sources"
    # Optional path pattern with placeholders:
    # {folder}, {source_slug}, {concept_slug}, {source_title}, {source_type}, {date}, {year}, {month}, {day}
    note_output_pattern: str | None = None
    # nested (default) → {folder}/{source_slug}/{concept_slug}.md; flat → {folder}/{concept_slug}.md
    note_output_layout: str = "nested"
    # Optional template with {{body}}, {{title}}, {{concept}}, {{tags}}, etc.
    note_template_path: Path | None = None
    note_frontmatter_type: str = "atomic"
    note_frontmatter_status: str = "draft"
    # Propose a map-of-content index note when at least this many notes are drafted.
    generate_moc: bool = True
    moc_min_notes: int = 2

    # Obsidian integration: vault display name for obsidian:// URIs; auto-use .obsidian templates.
    obsidian_vault_name: str | None = None
    use_obsidian_templates: bool = True
    obsidian_template_name: str | None = None

    # Richer vault embeddings: prepend title + aliases to indexed chunk text.
    rich_note_embeddings: bool = True
    # Boost similarity when source/vault tags overlap (reduces false "Novel").
    tag_similarity_enabled: bool = True
    tag_similarity_boost_per_tag: float = 0.06
    tag_similarity_max_boost: float = 0.15
    # Threshold assistant: sample size when calibrating from the index.
    threshold_calibration_samples: int = 200
    # Append Obsidian ^block-id refs to bullets/paragraphs on write.
    include_block_ids: bool = False
    # Cross-link notes drafted from the same source to their most similar
    # siblings (Zettelkasten-style local graph, in addition to the MOC index).
    link_sibling_notes: bool = True
    # Maximum sibling wikilinks added to each note when linking is enabled.
    sibling_link_count: int = 3

    # Intelligence & automation (Phase 6)
    # Inject short vault excerpts into LLM draft prompts for better cross-refs.
    draft_rag_enabled: bool = True
    draft_rag_top_k: int = 3
    draft_rag_excerpt_chars: int = 400
    # Flag near-duplicate proposed notes using embedding similarity.
    duplicate_detection_enabled: bool = True
    duplicate_similarity_threshold: float = 0.85
    # Heuristic 0–1 quality score on drafted notes (structure / related links).
    note_quality_scoring_enabled: bool = True
    # Optional LLM "deep read" claims pass before single-note synthesize.
    # Default off: extractive EvidencePack only. Skipped when batch drafting
    # (llm_draft_batch_size > 1), topic is not novel, or budget is low.
    draft_llm_deep_read: bool = False

    # When > 0, inline bounded excerpts from ![[embedded]] notes into embeddings.
    transclude_depth: int = 0
    transclude_excerpt_chars: int = 400

    # Re-index changed vault notes automatically when .md files are saved (watchdog).
    vault_watch_enabled: bool = False
    vault_watch_debounce_seconds: float = 5.0
    # When analyzing an existing vault note, default to append when overlap targets it.
    analyze_in_place_enabled: bool = True

    # Phase E — quality & cost
    # Draft N topics per LLM call (1 = one call per note; no batch phase).
    # When > 1, failed/missing batch items use extractive fallback — never a
    # second per-note LLM call (avoids double-spend after parse failures).
    llm_draft_batch_size: int = 3
    # Max LLM topic-planning calls per run (map-reduce over large sources; 0 =
    # unlimited, still bounded by the per-run LLM caps). 1 keeps the old
    # single-window behavior; windows beyond the cap are planned structurally.
    llm_max_planning_calls: int = 6
    # Keep separate Chroma collections per vault path (switch vaults without rebuild).
    multi_vault_index_enabled: bool = False
    # When appending to an overlapping note, insert under the matched chunk heading.
    append_under_overlap_heading: bool = True
    # Commit only notes written by an apply request when the vault is a Git repo.
    git_auto_commit_on_apply: bool = False
    git_commit_message: str = "Actualize notes from {source}"

    # Optional cloud LLM for drafting. Leave unset to use extractive fallback.
    # Configure via .env — never commit API keys.
    # Providers: openai | anthropic | gemini | ollama (local alias of ollama)
    llm_provider: str | None = None
    llm_api_key: str | None = None
    llm_model: str | None = None
    ollama_base_url: str = "http://127.0.0.1:11434"

    # Optional shared secret. When set, /api/* requires
    # Authorization: Bearer <token> or X-API-Token: <token>.
    # Required when BIND_HOST is a non-loopback address (e.g. 0.0.0.0 in Docker).
    api_token: str | None = None
    # Comma-separated capability names (read, analyze, write, admin, chat).
    # Empty = token grants all capabilities (backward compatible).
    api_token_capabilities: str = ""
    disable_plugin_discovery: bool = False
    plugin_allowlist: str = ""

    # Uvicorn bind address. Non-loopback values require API_TOKEN at startup.
    bind_host: str = "127.0.0.1"

    # Comma-separated Host header allowlist (DNS-rebinding guard). Add your
    # hostname when serving beyond localhost; empty disables the check.
    allowed_hosts: str = "localhost,127.0.0.1,[::1]"

    # Rate-limit handling: retry a throttled LLM/embedding call with exponential
    # backoff (base * 2**attempt, capped at max) before giving up.
    llm_max_retries: int = 5
    llm_retry_base_delay: float = 5.0
    llm_retry_max_delay: float = 60.0
    # Disable the LLM for the rest of a run after this many notes in a row
    # exhaust their retries (quota likely gone); they fall back to extractive.
    llm_disable_after_failures: int = 2

    # Hard caps per analyze run (0 = unlimited). Counts every LLM complete()
    # (topic planning + note drafts). Input chars are a rough spend proxy.
    llm_max_calls_per_run: int = 50
    llm_max_input_chars_per_run: int = 400_000

    @model_validator(mode="after")
    def validate_thresholds(self):
        if not (0.0 <= self.novel_threshold < self.known_threshold <= 1.0):
            raise ValueError(
                "Require 0 <= NOVEL_THRESHOLD < KNOWN_THRESHOLD <= 1; "
                f"got novel={self.novel_threshold}, known={self.known_threshold}"
            )
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError(
                f"CHUNK_OVERLAP ({self.chunk_overlap}) must be smaller than "
                f"CHUNK_SIZE ({self.chunk_size})"
            )
        if self.embedding_query_batch_size < 1:
            raise ValueError("EMBEDDING_QUERY_BATCH_SIZE must be >= 1")
        self.embedding_device = self.embedding_device.strip().lower()
        if self.embedding_device not in {"auto", "cpu", "cuda", "mps"}:
            raise ValueError("EMBEDDING_DEVICE must be auto, cpu, cuda, or mps")
        self.embedding_backend = self.embedding_backend.strip().lower()
        if self.embedding_backend not in {"sentence_transformers", "onnx"}:
            raise ValueError(
                "EMBEDDING_BACKEND must be 'sentence_transformers' or 'onnx'"
            )
        if self.chroma_index_batch_size < 1:
            raise ValueError("CHROMA_INDEX_BATCH_SIZE must be >= 1")
        self.vector_backend = self.vector_backend.strip().lower()
        if self.vector_backend not in {"chroma", "qdrant"}:
            raise ValueError("VECTOR_BACKEND must be 'chroma' or 'qdrant'")
        if self.qdrant_vector_size < 1:
            raise ValueError("QDRANT_VECTOR_SIZE must be >= 1")
        if not self.qdrant_collection.strip():
            raise ValueError("QDRANT_COLLECTION must not be empty")
        if not self.git_commit_message.strip():
            raise ValueError("GIT_COMMIT_MESSAGE must not be empty")
        if self.llm_max_calls_per_run < 0:
            raise ValueError("LLM_MAX_CALLS_PER_RUN must be >= 0 (0 = unlimited)")
        if self.llm_max_input_chars_per_run < 0:
            raise ValueError("LLM_MAX_INPUT_CHARS_PER_RUN must be >= 0 (0 = unlimited)")
        if self.max_upload_mb < 0:
            raise ValueError("MAX_UPLOAD_MB must be >= 0 (0 = unlimited)")
        if self.max_fetch_mb < 0:
            raise ValueError("MAX_FETCH_MB must be >= 0 (0 = unlimited)")
        if self.whisper_beam_size < 1:
            raise ValueError("WHISPER_BEAM_SIZE must be >= 1")
        if self.auto_tagging_top_k < 1 or self.auto_tagging_max_tags < 1:
            raise ValueError("AUTO_TAGGING_TOP_K and AUTO_TAGGING_MAX_TAGS must be >= 1")
        if self.pdf_ocr_dpi < 72:
            raise ValueError("PDF_OCR_DPI must be >= 72")
        if self.moc_min_notes < 2:
            raise ValueError("MOC_MIN_NOTES must be >= 2")
        if self.tag_similarity_boost_per_tag < 0:
            raise ValueError("TAG_SIMILARITY_BOOST_PER_TAG must be >= 0")
        if self.tag_similarity_max_boost < 0:
            raise ValueError("TAG_SIMILARITY_MAX_BOOST must be >= 0")
        if self.threshold_calibration_samples < 20:
            raise ValueError("THRESHOLD_CALIBRATION_SAMPLES must be >= 20")
        self.log_level = self.log_level.strip().upper()
        if self.log_level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError("LOG_LEVEL must be DEBUG, INFO, WARNING, ERROR, or CRITICAL")
        self.log_format = self.log_format.strip().lower()
        if self.log_format not in {"plain", "json"}:
            raise ValueError("LOG_FORMAT must be 'plain' or 'json'")
        if self.transclude_depth < 0:
            raise ValueError("TRANSCLUDE_DEPTH must be >= 0 (0 = disabled)")
        if self.transclude_excerpt_chars < 1:
            raise ValueError("TRANSCLUDE_EXCERPT_CHARS must be >= 1")
        if self.note_output_layout.strip().lower() not in {"nested", "flat"}:
            raise ValueError("NOTE_OUTPUT_LAYOUT must be 'nested' or 'flat'")
        if self.vault_watch_debounce_seconds <= 0:
            raise ValueError("VAULT_WATCH_DEBOUNCE_SECONDS must be > 0")
        if self.llm_draft_batch_size < 1:
            raise ValueError("LLM_DRAFT_BATCH_SIZE must be >= 1")
        if self.draft_rag_top_k < 1:
            raise ValueError("DRAFT_RAG_TOP_K must be >= 1")
        if self.draft_rag_excerpt_chars < 80:
            raise ValueError("DRAFT_RAG_EXCERPT_CHARS must be >= 80")
        if not (0.0 < self.duplicate_similarity_threshold <= 1.0):
            raise ValueError("DUPLICATE_SIMILARITY_THRESHOLD must be in (0, 1]")
        if self.llm_provider is not None:
            self.llm_provider = self.llm_provider.strip() or None
        if self.llm_model is not None:
            self.llm_model = self.llm_model.strip() or None
        if self.llm_api_key is not None:
            self.llm_api_key = self.llm_api_key.strip() or None
        if self.qdrant_url is not None:
            self.qdrant_url = self.qdrant_url.strip() or None
        if self.qdrant_api_key is not None:
            self.qdrant_api_key = self.qdrant_api_key.strip() or None
        if self.vision_model is not None:
            self.vision_model = self.vision_model.strip() or None
        if self.whisper_language is not None:
            self.whisper_language = self.whisper_language.strip() or None
        self.prompt_domain = self.prompt_domain.strip().lower()
        parse_api_token_capabilities(self.api_token_capabilities)
        return self

    @property
    def allowed_host_set(self) -> frozenset[str]:
        return frozenset(
            host.strip().lower() for host in self.allowed_hosts.split(",") if host.strip()
        )

    @property
    def allowed_vault_root_paths(self) -> tuple[Path, ...]:
        roots: list[Path] = []
        for raw in self.allowed_vault_roots.split(","):
            item = raw.strip()
            if not item:
                continue
            roots.append(Path(item).expanduser().resolve())
        return tuple(roots)

    @staticmethod
    def is_loopback_bind_host(host: str) -> bool:
        value = (host or "").strip().lower()
        if not value:
            return True
        if value in {"127.0.0.1", "localhost", "::1", "[::1]"}:
            return True
        try:
            import ipaddress

            return ipaddress.ip_address(value.strip("[]")).is_loopback
        except ValueError:
            return False

    def require_api_token_for_bind_host(self) -> None:
        """Fail fast when binding publicly without an API token."""
        if self.is_loopback_bind_host(self.bind_host):
            return
        if self.api_token and self.api_token.strip():
            return
        raise RuntimeError(
            f"API_TOKEN is required when BIND_HOST={self.bind_host!r} "
            "(non-loopback). Set a strong token or bind to 127.0.0.1."
        )

    @property
    def default_note_tags_list(self) -> list[str]:
        if not self.default_note_tags.strip():
            return []
        return [
            tag.strip().lstrip("#")
            for tag in self.default_note_tags.split(",")
            if tag.strip()
        ]

    @property
    def chroma_path(self) -> Path:
        return self.data_dir / "chroma"

    @property
    def graph_cache_path(self) -> Path:
        return self.data_dir / "graph.json"

    @property
    def plugin_allowlist_set(self) -> frozenset[str]:
        return frozenset(
            name.strip()
            for name in self.plugin_allowlist.split(",")
            if name.strip()
        )

    @property
    def api_token_capability_set(self) -> frozenset[str]:
        return parse_api_token_capabilities(self.api_token_capabilities)

    @property
    def embeddings(self) -> EmbeddingsSettings:
        return EmbeddingsSettings(
            provider=self.embedding_provider,
            model=self.embedding_model,
            device=self.embedding_device,
            backend=self.embedding_backend,
            quantized=self.embedding_quantized,
            api_key=self.embedding_api_key,
        )

    @property
    def vector(self) -> VectorSettings:
        return VectorSettings(
            backend=self.vector_backend,
            qdrant_url=self.qdrant_url,
            qdrant_api_key=self.qdrant_api_key,
            qdrant_collection=self.qdrant_collection,
            qdrant_vector_size=self.qdrant_vector_size,
        )

    @property
    def auth(self) -> AuthSettings:
        return AuthSettings(
            api_token=self.api_token,
            capabilities=self.api_token_capability_set,
            bind_host=self.bind_host,
            allowed_hosts=self.allowed_hosts,
        )

    @property
    def plugins(self) -> PluginSettings:
        return PluginSettings(
            disable_discovery=self.disable_plugin_discovery,
            allowlist=self.plugin_allowlist_set,
        )

    @property
    def is_networked_profile(self) -> bool:
        return not self.is_loopback_bind_host(self.bind_host)

    def require_networked_profile(self) -> None:
        """Fail fast when binding beyond localhost without hardened defaults."""
        if not self.is_networked_profile:
            return
        self.require_api_token_for_bind_host()
        if not self.allowed_vault_root_paths:
            raise RuntimeError(
                f"ALLOWED_VAULT_ROOTS must be set when BIND_HOST={self.bind_host!r} "
                "(non-loopback). Open vault mode is not allowed on networked binds."
            )
        if not self.disable_plugin_discovery and not self.plugin_allowlist_set:
            raise RuntimeError(
                "Set DISABLE_PLUGIN_DISCOVERY=true or PLUGIN_ALLOWLIST when binding "
                f"non-loopback ({self.bind_host!r})."
            )

    @property
    def llm_enabled(self) -> bool:
        if not self.llm_provider or not self.llm_model:
            return False
        provider = self.llm_provider.lower()
        # Ollama / local OpenAI-compatible servers do not require an API key.
        if provider in {"ollama", "local"}:
            return True
        return bool(self.llm_api_key)


settings = Settings()
