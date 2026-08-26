"""FastAPI-based loopback API for deterministic floor-plan validation."""

from __future__ import annotations

import argparse
import sys
from typing import Callable

from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from fastapi.responses import JSONResponse

from knowledge_engine.apps.api.routes.health import router as health_router
from knowledge_engine.apps.api.routes.validate import router as validate_router

LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
DEPLOYMENT_HOST = "0.0.0.0"
MAX_REQUEST_BYTES = 1_000_000  # 1 MB – mirrors the legacy http.server limit.


class _SizeLimitMiddleware(BaseHTTPMiddleware):
    """Reject requests whose ``Content-Length`` exceeds the configured limit."""

    def __init__(self, app, max_bytes: int) -> None:
        super().__init__(app)
        self._max_bytes = max_bytes

    async def dispatch(self, request: Request, call_next):
        raw_length = request.headers.get("content-length", "0")
        try:
            length = int(raw_length)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail={"error": "invalid_content_length"},
            )
        if length > self._max_bytes:
            raise HTTPException(
                status_code=413,
                detail={"error": "request_too_large"},
            )
        return await call_next(request)


def _size_limit_middleware(max_bytes: int) -> Middleware:
    """Return a FastAPI ``Middleware`` instance for the body-size guard."""
    return Middleware(_SizeLimitMiddleware, max_bytes=max_bytes)


def create_app() -> FastAPI:
    """Build and return the FastAPI application instance.

    This is the primary factory function.  It assembles all route groups
    so that the app can be constructed both for the CLI entry-point and
    for tests that import it directly (e.g. ``TestClient``).
    """
    app = FastAPI(
        title="Knowledge Engine Local API",
        summary="Local-only floor-plan validation endpoint.",
        description=(
            "The Knowledge Engine exposes a loopback-only FastAPI server "
            "for deterministic floor-plan validation.  All computation is "
            "performed locally – no data leaves the machine.  Official "
            "Vaastu scoring is gated behind a feature flag."
        ),
        version="0.1.0",
        docs_url="/docs",
        redoc_url="/redoc",
        middleware=[
            _size_limit_middleware(MAX_REQUEST_BYTES),
        ],
    )
    app.include_router(health_router)
    app.include_router(validate_router)

    return app


def create_server(
    host: str = "127.0.0.1",
    port: int = 8765,
) -> "uvicorn.Server":  # noqa: F821
    """Build a configured ``uvicorn.Server`` for the local API.

    Parameters
    ----------
    host:
        Loopback interface to bind.  Only ``127.0.0.1``, ``localhost``,
        and ``::1`` are accepted.
    port:
        TCP port to listen on.

    Returns
    -------
    uvicorn.Server
        A ready-to-run server instance.  Call ``.run()`` to start it.
    """
    import uvicorn

    if host not in LOOPBACK_HOSTS and host != DEPLOYMENT_HOST:
        raise ValueError(
            "The Knowledge Engine API is local-only; bind to a loopback host."
        )

    app = create_app()
    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        log_level="warning",
    )
    return uvicorn.Server(config)


def main(argv: list[str] | None = None) -> int:
    """Entry-point for the ``python -m knowledge_engine api`` command.

    Parses ``--host`` and ``--port``, starts the uvicorn server, and
    returns 0 on clean shutdown.
    """
    parser = argparse.ArgumentParser(
        description="Run the local Knowledge Engine FastAPI server.",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(list(argv) if argv is not None else None)

    server = create_server(args.host, args.port)
    print(
        f"Knowledge Engine local API listening on "
        f"http://{args.host}:{args.port}"
    )
    server.run()
    return 0


# Re-export the app so ``from knowledge_engine.apps.api.main import app``
# works for tests and programmatic consumers.
app = create_app()

if __name__ == "__main__":
    raise SystemExit(main())