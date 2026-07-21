# Contributing

## Development setup

Use Python 3.10 or newer. The setup script safely creates or updates `.venv`,
installs the development requirements, and adds missing variables to `.env`
without overwriting local values:

```bash
./scripts/setup_dev.sh
.venv/bin/uvicorn app.main:app --reload
```

PDF OCR is optional. Install `requirements-ocr.txt` plus the Poppler and
Tesseract system packages when working on scanned-document support.

## Tests and checks

Run the focused test first, then the complete checks before submitting:

```bash
.venv/bin/pytest tests/test_pdf_loader.py
.venv/bin/pytest tests/
.venv/bin/ruff check app tests
.venv/bin/mypy app
node --test frontend/app-core.test.js
npm --prefix obsidian-plugin test
```

Tests should be hermetic: mock network services and expensive document tools,
and prefer minimal generated fixtures over committed binaries.

## Environment synchronization

`app/config.py` is the source of truth for settings. After changing defaults,
regenerate and verify the committed template:

```bash
.venv/bin/python scripts/env_sync.py generate
.venv/bin/python scripts/env_sync.py check
```

Run `.venv/bin/python scripts/env_sync.py merge` to append new variables to
your local `.env`. It never replaces existing values or secrets. Do not edit
`.env.example` manually or commit `.env`.

## Style

Follow the existing type-annotated Python style, keep loaders deterministic,
and make optional integrations fail gracefully. Ruff is the formatting and
lint baseline; avoid unrelated formatting or refactors in focused changes.

## Extension entry points

External packages may register loader instances/classes under
`actualizer.source_loaders`. A loader should implement `supports_path` and
`load_from_path`, or `supports_url` and `load_from_url`. LLM plugins register a
factory under `actualizer.llm_providers`; the factory receives keyword arguments
`model`, `api_key`, and `settings` and returns an `LLMProvider`-compatible object.
Discovery failures are logged and do not prevent startup.

## Security

Never commit API tokens, vault contents, uploaded documents, or generated
indexes. Treat source files, URLs, archive members, and vault paths as
untrusted input. Preserve upload limits, URL validation, path containment,
host checks, and authentication behavior. Report suspected vulnerabilities
privately to the maintainers rather than opening a public issue with exploit
details.
