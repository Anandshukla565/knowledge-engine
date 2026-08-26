# Knowledge Engine API

FastAPI-based loopback server for the Knowledge Engine floor-plan validation pipeline.

## Overview

This package replaces the legacy `http.server`-based API with a [FastAPI](https://fastapi.tiangolo.com/) application.  It exposes three endpoints, all of which are **local-only** (the server binds to `127.0.0.1` by default and rejects non-loopback hosts).

| Method | Path          | Description                                   |
|--------|---------------|-----------------------------------------------|
| GET    | `/health`     | Liveness check + capabilities snapshot        |
| GET    | `/v1/capabilities` | Full API capabilities descriptor         |
| POST   | `/v1/validate` | Validate an inline floor-plan JSON object    |

Interactive API docs are available at `/docs` (Swagger UI) and `/redoc` (ReDoc) when the server is running.

## Prerequisites

```bash
pip install -r requirements-fastapi.txt
```

Or install FastAPI + uvicorn directly:

```bash
pip install fastapi uvicorn[standard]
```

## Running the server

### Via the CLI (recommended)

The server is wired into the existing ``knowledge-engine`` entry-point:

```bash
# Defaults: 127.0.0.1:8765
python -m knowledge_engine api

# Custom bind address and port
python -m knowledge_engine api --host 127.0.0.1 --port 9000
```

### Via uvicorn directly

```bash
uvicorn knowledge_engine.apps.api.main:app --host 127.0.0.1 --port 8765
```

> **Note** – The `app` object is exported from `knowledge_engine.apps.api.main` so
> any ASGI server can import and run it directly.

### From Python

```python
from knowledge_engine.apps.api.main import create_server

server = create_server(host="127.0.0.1", port=8765)
server.run()  # blocking
```

## Example requests

### Health check

```bash
curl -s http://127.0.0.1:8765/health | python -m json.tool
```

### Capabilities

```bash
curl -s http://127.0.0.1:8765/v1/capabilities | python -m json.tool
```

### Validate a floor plan

```bash
curl -s -X POST http://127.0.0.1:8765/v1/validate \
  -H "Content-Type: application/json" \
  -d '{
    "floor_plan": {
      "rooms": [
        {"name": "living", "width_m": 4.2, "depth_m": 3.8},
        {"name": "kitchen", "width_m": 3.0, "depth_m": 2.5}
      ],
      "plot_width_m": 12.0,
      "plot_depth_m": 10.0
    }
  }' | python -m json.tool
```

### Using Python ``requests``

```python
import requests

resp = requests.post(
    "http://127.0.0.1:8765/v1/validate",
    json={
        "floor_plan": {
            "rooms": [
                {"name": "living", "width_m": 4.2, "depth_m": 3.8},
            ],
            "plot_width_m": 12.0,
            "plot_depth_m": 10.0,
        }
    },
    timeout=60,
)
print(resp.status_code)
print(resp.json()["validation_report"]["validation_status"])
```

## Error responses

All errors follow the envelope:

```json
{
  "error": "machine_readable_code",
  "message": "Human-readable detail when available."
}
```

| Code | Condition                       |
|------|---------------------------------|
| `invalid_request`         | Malformed JSON or missing `floor_plan` |
| `request_body_required`   | Empty POST body                 |
| `request_too_large`       | Body exceeds 1 MB               |
| `invalid_content_length`  | Non-numeric `Content-Length` header |

## Architecture

```
knowledge_engine/apps/api/
├── main.py          # FastAPI app factory + CLI entry-point (replaces legacy http.server)
├── service.py       # Business logic – unchanged
├── schemas.py       # Pydantic v2 request / response models
├── routes/
│   ├── __init__.py
│   ├── health.py    # GET /health, GET /v1/capabilities
│   └── validate.py  # POST /v1/validate
└── README.md        # This file
```

## Security

* The server binds to **loopback only** – passing a non-loopback host raises ``ValueError``.
* No data is written to SQLite or external services during validation.
* The validation pipeline writes temporary files that are deleted at the end of each request.
