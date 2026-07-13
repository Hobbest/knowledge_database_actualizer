"""Generate and sync environment files from :class:`app.config.Settings`.

``Settings`` in ``app/config.py`` is the single source of truth for defaults.
This module never reads secrets from ``.env`` for generation — only field names
and defaults from the Pydantic model.

- ``.env.example`` — generated, committed, no real API keys
- ``.env`` — gitignored; use :func:`merge_local_env` to add new keys without
  overwriting values you already set
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.config import Settings

# Never emit non-empty values for these in generated / merged template lines.
SECRET_FIELDS = frozenset({"llm_api_key", "embedding_api_key", "api_token"})

ROOT = Path(__file__).resolve().parent.parent
ENV_EXAMPLE_PATH = ROOT / ".env.example"
LOCAL_ENV_PATH = ROOT / ".env"


@dataclass(frozen=True)
class EnvSection:
    header: tuple[str, ...]
    fields: tuple[str, ...]


# Ordered layout for .env.example (every Settings field must appear exactly once).
ENV_SECTIONS: tuple[EnvSection, ...] = (
    EnvSection(
        header=("# Path to your Obsidian vault (markdown files)",),
        fields=("vault_path",),
    ),
    EnvSection(
        header=("# Local data directory for Chroma and app state",),
        fields=("data_dir",),
    ),
    EnvSection(
        header=(
            "# Embeddings (used for novelty/similarity search — separate from LLM note drafting)",
            "# EMBEDDING_PROVIDER: local (sentence-transformers) or gemini",
            "# Local models: all-MiniLM-L6-v2 (default, fast), all-mpnet-base-v2, bge-small-en-v1.5",
        ),
        fields=("embedding_provider", "embedding_model"),
    ),
    EnvSection(
        header=("# Optional Gemini embedding API key (falls back to LLM_API_KEY)",),
        fields=("embedding_api_key",),
    ),
    EnvSection(
        header=(
            "# Novelty thresholds (cosine similarity, 0-1)",
            "# Below NOVEL_THRESHOLD = novel chunk; above KNOWN_THRESHOLD = already known",
            "# Recommended starting points:",
            "#   local  all-MiniLM-L6-v2:     NOVEL=0.55  KNOWN=0.75",
            "#   local  all-mpnet-base-v2:    NOVEL=0.58  KNOWN=0.78",
            "#   local  bge-small-en-v1.5:    NOVEL=0.60  KNOWN=0.80",
            "#   gemini gemini-embedding-001: NOVEL=0.65  KNOWN=0.82",
        ),
        fields=("novel_threshold", "known_threshold"),
    ),
    EnvSection(
        header=(
            "# How many query texts to embed/search together (novelty + segment scoring)",
        ),
        fields=("embedding_query_batch_size",),
    ),
    EnvSection(
        header=("# How many chunks to embed+write per batch during full vault indexing",),
        fields=("chroma_index_batch_size",),
    ),
    EnvSection(
        header=(
            "# Chunking (characters) for vault indexing and novelty comparison.",
            "# Chunks respect paragraph/list/code boundaries before the size cap.",
            "# Chunks must fit the embedding model's input window, or the tail of every",
            "# chunk is silently ignored when similarity is computed:",
            "#   local  (all-MiniLM-L6-v2):     256 tokens  ≈ 1000 chars",
            "#   local  (bge-small-en-v1.5):    512 tokens  ≈ 2048 chars",
            "#   gemini (gemini-embedding-001): 2048 tokens ≈ 8000 chars",
            "# The app refuses to start when CHUNK_SIZE exceeds the model's limit.",
        ),
        fields=("chunk_size", "chunk_overlap"),
    ),
    EnvSection(
        header=("# Atomic note generation",),
        fields=(
            "max_note_lines",
            "max_notes_per_source",
            "atomic_note_line_limit",
            "atomic_note_char_limit",
        ),
    ),
    EnvSection(
        header=(
            "# Planning segmentation.",
            "# Large source units (e.g. full PDF pages) are split into planning units of",
            "# about this many characters before notes are planned. LOWER this to get MORE",
            "# notes from big PDFs/videos (e.g. 800); raise it for fewer, broader notes.",
        ),
        fields=("segment_target_chars",),
    ),
    EnvSection(
        header=("# Reject uploads larger than this many megabytes (0 = unlimited).",),
        fields=("max_upload_mb",),
    ),
    EnvSection(
        header=(
            "# Skip non-substantive sections (acknowledgements, table of contents, reference",
            "# lists, chapter summaries, contact/copyright pages) when generating notes.",
            "# Set to false to keep every section.",
        ),
        fields=("filter_boilerplate",),
    ),
    EnvSection(
        header=(
            "# Detect tables (real PDF tables via pdfplumber + markdown pipe tables) and",
            "# figure captions, and attach the ones on a note's page/lines to that note.",
        ),
        fields=("include_media",),
    ),
    EnvSection(
        header=(
            "# Comma-separated tags added to every drafted note (Obsidian frontmatter)",
        ),
        fields=("default_note_tags",),
    ),
    EnvSection(
        header=(
            "# Where new notes are written in the vault (folder prefix)",
            "# Optional Obsidian template with {{body}}, {{title}}, {{concept}}, {{tags}}, etc.",
            "# Frontmatter fields on drafted notes; MOC index when >= MOC_MIN_NOTES notes",
        ),
        fields=(
            "note_output_folder",
            "note_output_pattern",
            "note_output_layout",
            "note_template_path",
            "note_frontmatter_type",
            "note_frontmatter_status",
            "generate_moc",
            "moc_min_notes",
        ),
    ),
    EnvSection(
        header=(
            "# Vault workflow (Phase D)",
            "# VAULT_WATCH_ENABLED — debounced re-index when .md files change in VAULT_PATH",
            "# ANALYZE_IN_PLACE_ENABLED — append to the analyzed note when overlap targets it",
        ),
        fields=(
            "vault_watch_enabled",
            "vault_watch_debounce_seconds",
            "analyze_in_place_enabled",
        ),
    ),
    EnvSection(
        header=(
            "# Obsidian integration",
            "# OBSIDIAN_VAULT_NAME — display name for obsidian://open links in the web UI",
            "# USE_OBSIDIAN_TEMPLATES — auto-pick a template from the vault's template folder",
            "# OBSIDIAN_TEMPLATE_NAME — optional specific template filename",
            "# TRANSCLUDE_DEPTH — when 1, embed ![[note]] excerpts in indexed text (0 = off)",
        ),
        fields=(
            "obsidian_vault_name",
            "use_obsidian_templates",
            "obsidian_template_name",
            "transclude_depth",
            "transclude_excerpt_chars",
        ),
    ),
    EnvSection(
        header=(
            "# Quality & cost (Phase E)",
            "# LLM_DRAFT_BATCH_SIZE — draft N topics per LLM call (1 = one note per call)",
            "# MULTI_VAULT_INDEX — separate Chroma collections per vault path",
            "# APPEND_UNDER_OVERLAP_HEADING — insert append content under matched section",
        ),
        fields=(
            "llm_draft_batch_size",
            "multi_vault_index_enabled",
            "append_under_overlap_heading",
        ),
    ),
    EnvSection(
        header=(
            "# Quality & intelligence (Phase 4)",
            "# RICH_NOTE_EMBEDDINGS — prepend title + aliases to indexed chunk text",
            "# Tag overlap boost for novelty when source/vault tags match",
            "# THRESHOLD_CALIBRATION_SAMPLES — chunk pairs sampled for threshold assistant",
            "# INCLUDE_BLOCK_IDS — append Obsidian ^block-id refs on write",
        ),
        fields=(
            "rich_note_embeddings",
            "tag_similarity_enabled",
            "tag_similarity_boost_per_tag",
            "tag_similarity_max_boost",
            "threshold_calibration_samples",
            "include_block_ids",
        ),
    ),
    EnvSection(
        header=(
            "# Optional LLM for drafting notes (leave empty to use extractive fallback)",
            "# Providers: openai | anthropic | gemini | ollama (local is an alias of ollama)",
        ),
        fields=("llm_provider", "llm_api_key", "llm_model"),
    ),
    EnvSection(
        header=("# Ollama / local OpenAI-compatible server (no API key required)",),
        fields=("ollama_base_url",),
    ),
    EnvSection(
        header=(
            "# Optional shared secret for non-localhost deploys. When set, all /api/*",
            "# requests need Authorization: Bearer <token> or X-API-Token: <token>.",
            "# The SPA shell (/) stays public; the UI prompts for the token on 401.",
        ),
        fields=("api_token",),
    ),
    EnvSection(
        header=(
            "# Host header allowlist (comma-separated) — blocks DNS-rebinding attacks that",
            "# point an attacker-controlled hostname at 127.0.0.1. Add your hostname when",
            "# serving beyond localhost; leave empty to disable the check.",
        ),
        fields=("allowed_hosts",),
    ),
    EnvSection(
        header=(
            "# Rate-limit handling. A throttled note is retried with exponential backoff",
            "# (base * 2**attempt, capped at max) before falling back to an extractive",
            "# summary. Increase retries/delays for strict free-tier quotas.",
        ),
        fields=(
            "llm_max_retries",
            "llm_retry_base_delay",
            "llm_retry_max_delay",
            "llm_disable_after_failures",
        ),
    ),
    EnvSection(
        header=(
            "# Per-run LLM spend caps (0 = unlimited). Each note draft is one call; topic",
            "# planning may add one more. Input chars ≈ tokens × 4 for English text.",
        ),
        fields=("llm_max_calls_per_run", "llm_max_input_chars_per_run"),
    ),
)

ENV_APPENDIX = """
# OpenAI example:
# LLM_PROVIDER=openai
# LLM_API_KEY=sk-...
# LLM_MODEL=gpt-4o-mini

# Anthropic example:
# LLM_PROVIDER=anthropic
# LLM_API_KEY=sk-ant-...
# LLM_MODEL=claude-3-5-haiku-latest

# Gemini example (chat model for note drafting):
# LLM_PROVIDER=gemini
# LLM_API_KEY=your-gemini-api-key
# LLM_MODEL=gemini-2.0-flash

# Gemini embeddings example (optional, for similarity search):
# EMBEDDING_PROVIDER=gemini
# EMBEDDING_MODEL=gemini-embedding-001
# EMBEDDING_API_KEY=your-gemini-api-key
# NOVEL_THRESHOLD=0.65
# KNOWN_THRESHOLD=0.82

# Local Ollama example (uncomment to use instead of cloud LLM):
# LLM_PROVIDER=ollama
# LLM_MODEL=llama3.2
# OLLAMA_BASE_URL=http://127.0.0.1:11434
""".strip()


def field_to_env_name(field_name: str) -> str:
    return field_name.upper()


def env_name_to_field(env_name: str) -> str:
    return env_name.strip().lower()


def _ordered_fields() -> list[str]:
    fields: list[str] = []
    for section in ENV_SECTIONS:
        fields.extend(section.fields)
    return fields


def validate_env_layout() -> None:
    """Ensure every Settings field is listed once in ENV_SECTIONS."""
    model_fields = set(Settings.model_fields)
    layout_fields = set(_ordered_fields())
    missing = sorted(model_fields - layout_fields)
    extra = sorted(layout_fields - model_fields)
    if missing or extra:
        parts: list[str] = []
        if missing:
            parts.append(f"missing from ENV_SECTIONS: {', '.join(missing)}")
        if extra:
            parts.append(f"unknown in ENV_SECTIONS: {', '.join(extra)}")
        raise ValueError("ENV_SECTIONS out of sync with Settings: " + "; ".join(parts))


def default_value_for_field(field_name: str) -> str:
    """Return the template value for a field (never loads ``.env``)."""
    if field_name in SECRET_FIELDS:
        return ""
    field = Settings.model_fields[field_name]
    value = field.default
    if value is None and field.default_factory is not None:
        value = field.default_factory()  # type: ignore[misc]
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, Path):
        return str(value)
    return str(value)


def generate_env_example() -> str:
    """Build ``.env.example`` content from Settings field defaults (no secrets)."""
    validate_env_layout()
    lines: list[str] = []
    for section in ENV_SECTIONS:
        if section.header:
            lines.extend(section.header)
        for field_name in section.fields:
            env_name = field_to_env_name(field_name)
            value = default_value_for_field(field_name)
            lines.append(f"{env_name}={value}")
        lines.append("")
    lines.append(ENV_APPENDIX)
    lines.append("")
    return "\n".join(lines)


def parse_env_assignments(text: str) -> dict[str, str]:
    """Parse KEY=value lines; comments and blanks are ignored. Last duplicate wins."""
    values: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[env_name_to_field(key)] = value.strip()
    return values


def merge_local_env(
    *,
    example_path: Path = ENV_EXAMPLE_PATH,
    env_path: Path = LOCAL_ENV_PATH,
    dry_run: bool = False,
) -> list[str]:
    """Add keys from ``.env.example`` that are missing in ``.env``.

    Existing ``.env`` values (including API keys) are never overwritten.
    Returns env var names that would be / were added.
    """
    if not example_path.is_file():
        raise FileNotFoundError(f"Missing {example_path}")
    example_values = parse_env_assignments(example_path.read_text(encoding="utf-8"))
    if env_path.is_file():
        local_values = parse_env_assignments(env_path.read_text(encoding="utf-8"))
    else:
        local_values = {}

    missing = [field_to_env_name(name) for name in example_values if name not in local_values]
    if not missing or dry_run:
        return missing

    additions: list[str] = [
        "",
        "# --- added by scripts/env_sync.py (existing values were not changed) ---",
    ]
    for env_name in missing:
        field = env_name_to_field(env_name)
        value = example_values[field]
        additions.append(f"{env_name}={value}")

    with env_path.open("a", encoding="utf-8") as handle:
        handle.write("\n".join(additions) + "\n")
    return missing


def write_env_example(path: Path = ENV_EXAMPLE_PATH) -> None:
    path.write_text(generate_env_example(), encoding="utf-8")


def env_example_is_current(path: Path = ENV_EXAMPLE_PATH) -> bool:
    if not path.is_file():
        return False
    return path.read_text(encoding="utf-8") == generate_env_example()
