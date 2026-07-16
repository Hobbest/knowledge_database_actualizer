"""Guards against config drift.

``Settings`` in ``app/config.py`` is the source of truth. ``.env.example`` is
generated from it (see ``scripts/env_sync.py``). Local ``.env`` is gitignored
and never committed.
"""

from __future__ import annotations

import pytest
from app.config import Settings, settings
from app.embeddings import CHARS_PER_TOKEN, chunk_size_error, max_embedding_input_chars
from app.env_sync import (
    ENV_EXAMPLE_PATH,
    env_example_is_current,
    generate_env_example,
    parse_env_assignments,
    validate_env_layout,
)


def test_env_layout_covers_all_settings_fields():
    validate_env_layout()


def test_env_example_matches_generated_output():
    assert ENV_EXAMPLE_PATH.is_file(), "Run: python scripts/env_sync.py generate"
    assert env_example_is_current(), (
        ".env.example is stale. Run: python scripts/env_sync.py generate"
    )


def test_generated_example_values_match_settings_defaults():
    """Non-empty generated values must equal Settings defaults (secrets stay empty)."""
    fields = Settings.model_fields
    generated = parse_env_assignments(generate_env_example())
    mismatches: list[str] = []

    for key, raw in generated.items():
        assert key in fields, f"generated key {key.upper()} has no Settings field"
        if not raw:
            continue
        default = fields[key].default
        if isinstance(default, bool):
            expected = raw.lower() in {"1", "true", "yes", "on"}
            if default is not expected:
                mismatches.append(f"{key.upper()}: generated={raw!r} vs default={default!r}")
        elif isinstance(default, (int, float)):
            if float(raw) != float(default):
                mismatches.append(f"{key.upper()}: generated={raw!r} vs default={default!r}")
        elif str(default) != raw:
            mismatches.append(f"{key.upper()}: generated={raw!r} vs default={default!r}")

    assert not mismatches, "Generated defaults drifted from Settings:\n" + "\n".join(mismatches)


def test_default_chunk_size_fits_default_embedding_model():
    """Chunks must fit the default model's input window, or novelty similarity
    is computed on silently truncated text."""
    fields = Settings.model_fields
    provider = fields["embedding_provider"].default
    model = fields["embedding_model"].default
    chunk_size = fields["chunk_size"].default

    limit = max_embedding_input_chars(provider, model)
    assert limit is not None, (
        f"Default embedding model {provider}/{model} is missing from "
        "_KNOWN_EMBEDDING_INPUT_TOKENS in app/embeddings.py"
    )
    assert chunk_size <= limit, (
        f"Default CHUNK_SIZE={chunk_size} exceeds ~{limit} chars "
        f"({limit // CHARS_PER_TOKEN} tokens) that {model} can embed"
    )
    assert fields["chunk_overlap"].default < chunk_size


def test_default_upload_limit_is_conservative():
    assert Settings.model_fields["max_upload_mb"].default == 50


def test_max_embedding_input_chars_unknown_model_is_none():
    assert max_embedding_input_chars("local", "some-unknown-model") is None


def test_chunk_size_error_flags_oversized_config(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "embedding_provider", "local")
    monkeypatch.setattr(settings, "embedding_model", "all-MiniLM-L6-v2")

    monkeypatch.setattr(settings, "chunk_size", 50000)
    error = chunk_size_error()
    assert error is not None and "50000" in error and "re-index" in error

    monkeypatch.setattr(settings, "chunk_size", 1000)
    assert chunk_size_error() is None
