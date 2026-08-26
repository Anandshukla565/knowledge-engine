from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from knowledge_engine.ai.tools import get_tool_capabilities, validate_floor_plan
from knowledge_engine.apps.api.main import create_server
from knowledge_engine.apps.api.service import validate_floor_plan_payload


def _sample_payload() -> dict:
    path = Path(__file__).parents[1] / "samples" / "valid_minimal_plan.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_local_service_validates_inline_data_without_official_scoring():
    result = validate_floor_plan_payload({"floor_plan": _sample_payload()})

    assert result["validation_report"]["validation_status"] == "pass"
    assert result["scorecard"]["vastu_score"] is None
    assert result["official_scoring_enabled"] is False
    assert result["official_vastu_scoring_used"] is False
    assert result["candidate_notes_used_for_scoring"] is False


def test_agent_tool_is_deterministic_and_has_no_path_input():
    capabilities = get_tool_capabilities()
    result = validate_floor_plan(_sample_payload())

    assert capabilities["api_scope"] == "local_only_loopback"
    assert capabilities["accepts_file_paths"] is False
    assert result["validation_report"]["geometry_valid"] is True
    assert result["scorecard"]["official_vastu_scoring_used"] is False


def test_local_service_rejects_missing_inline_plan():
    with pytest.raises(ValueError, match="floor_plan JSON object"):
        validate_floor_plan_payload({"input_path": "C:/not-accepted.json"})


def test_http_api_is_loopback_only_and_rejects_invalid_requests():
    with pytest.raises(ValueError, match="local-only"):
        create_server("0.0.0.0", 0)

    # Reserve a free port and bind the server to it explicitly.
    import socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    chosen_port = sock.getsockname()[1]
    sock.close()

    server = create_server("127.0.0.1", chosen_port)
    base_url = f"http://127.0.0.1:{chosen_port}"
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    # Poll until server responds (server.started is a bool, not an Event).
    for _ in range(50):
        try:
            urllib.request.urlopen(f"{base_url}/health", timeout=0.5).read()
            break
        except (urllib.error.URLError, ConnectionError):
            threading.Event().wait(0.2)
    try:
        health = json.loads(urllib.request.urlopen(f"{base_url}/health", timeout=5).read())
        assert health["status"] == "ok"
        assert health["official_scoring_enabled"] is False

        request = urllib.request.Request(
            f"{base_url}/v1/validate",
            data=json.dumps({"floor_plan": _sample_payload()}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        result = json.loads(urllib.request.urlopen(request, timeout=5).read())
        assert result["validation_report"]["validation_status"] == "pass"
        assert result["vastu_score"] is None

        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(f"{base_url}/missing", timeout=5)
        assert exc_info.value.code == 404
    finally:
        server.should_exit = True
        thread.join(timeout=5)
