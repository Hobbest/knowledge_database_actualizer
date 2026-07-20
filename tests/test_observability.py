import json
import logging

from app.observability import AppMetrics, JsonFormatter


def test_metrics_snapshot_tracks_requests_and_analyze_runs():
    metrics = AppMetrics()
    metrics.record_request(20, error=False)
    metrics.record_request(40, error=True)
    metrics.record_analyze(
        100,
        error=False,
        notes_drafted=3,
        llm_calls=2,
        cache_hits=1,
    )
    snapshot = metrics.snapshot()
    assert snapshot["requests"] == 2
    assert snapshot["request_errors"] == 1
    assert snapshot["average_request_ms"] == 30
    assert snapshot["notes_drafted"] == 3
    assert snapshot["llm_calls"] == 2
    assert snapshot["cache_hits"] == 1


def test_json_formatter_emits_structured_message():
    record = logging.LogRecord(
        "app.test",
        logging.INFO,
        __file__,
        1,
        "hello %s",
        ("world",),
        None,
    )
    payload = json.loads(JsonFormatter().format(record))
    assert payload["level"] == "INFO"
    assert payload["message"] == "hello world"
