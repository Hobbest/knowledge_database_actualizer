from pathlib import Path

import pytest
from app.config import settings
from app.settings_persistence import update_env_values
from fastapi.testclient import TestClient


def test_update_env_values_preserves_unrelated_values(tmp_path: Path):
    env = tmp_path / ".env"
    env.write_text(
        "# local configuration\nNOVEL_THRESHOLD=0.5\nAPI_TOKEN=keep-secret\n",
        encoding="utf-8",
    )
    update_env_values(
        env,
        {"NOVEL_THRESHOLD": "0.61", "KNOWN_THRESHOLD": "0.81"},
    )
    text = env.read_text(encoding="utf-8")
    assert "# local configuration" in text
    assert "NOVEL_THRESHOLD=0.61" in text
    assert "KNOWN_THRESHOLD=0.81" in text
    assert "API_TOKEN=keep-secret" in text


def test_threshold_endpoint_validates_and_applies_values(
    tmp_data_dir,
    monkeypatch: pytest.MonkeyPatch,
):
    from app import main

    writes = []
    monkeypatch.setattr(settings, "novel_threshold", 0.55)
    monkeypatch.setattr(settings, "known_threshold", 0.75)
    monkeypatch.setattr(main, "update_env_values", lambda path, values: writes.append(values))
    client = TestClient(main.app, base_url="http://127.0.0.1")
    response = client.post(
        "/api/vault/thresholds",
        json={"novel": 0.61, "known": 0.81, "persist": True},
    )
    assert response.status_code == 200
    assert response.json()["persisted"] is True
    assert settings.novel_threshold == 0.61
    assert writes == [{"NOVEL_THRESHOLD": "0.61", "KNOWN_THRESHOLD": "0.81"}]

    invalid = client.post(
        "/api/vault/thresholds",
        json={"novel": 0.9, "known": 0.8},
    )
    assert invalid.status_code == 400
