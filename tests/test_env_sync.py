from __future__ import annotations

from pathlib import Path

from app.env_sync import merge_local_env, parse_env_assignments


def test_merge_local_env_adds_missing_keys_without_overwriting(tmp_path: Path):
    example = tmp_path / ".env.example"
    env_file = tmp_path / ".env"

    example.write_text(
        "VAULT_PATH=\n"
        "DATA_DIR=./data\n"
        "LLM_API_KEY=\n"
        "LLM_MODEL=\n",
        encoding="utf-8",
    )
    env_file.write_text(
        "DATA_DIR=./custom\n"
        "LLM_API_KEY=sk-secret\n",
        encoding="utf-8",
    )

    added = merge_local_env(example_path=example, env_path=env_file)
    assert set(added) == {"VAULT_PATH", "LLM_MODEL"}

    local = parse_env_assignments(env_file.read_text(encoding="utf-8"))
    assert local["data_dir"] == "./custom"
    assert local["llm_api_key"] == "sk-secret"
    assert local["vault_path"] == ""
    assert local["llm_model"] == ""


def test_merge_local_env_dry_run_does_not_write(tmp_path: Path):
    example = tmp_path / ".env.example"
    env_file = tmp_path / ".env"
    example.write_text("NEW_KEY=value\n", encoding="utf-8")
    env_file.write_text("EXISTING=1\n", encoding="utf-8")
    before = env_file.read_text(encoding="utf-8")

    added = merge_local_env(example_path=example, env_path=env_file, dry_run=True)
    assert added == ["NEW_KEY"]
    assert env_file.read_text(encoding="utf-8") == before
