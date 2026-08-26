#!/usr/bin/env python3
"""Quick smoke-test for the FastAPI Knowledge Engine API."""

from __future__ import annotations

import sys

REQUIRED_PACKAGES = ["fastapi", "uvicorn", "pydantic"]


def check_imports() -> bool:
    print("=== Checking required packages ===")
    all_ok = True
    for pkg in REQUIRED_PACKAGES:
        try:
            mod = __import__(pkg)
            version = getattr(mod, "__version__", "unknown")
            print(f"  {pkg}: {version} OK")
        except ImportError:
            print(f"  {pkg}: MISSING (run: pip install {pkg})")
            all_ok = False
    return all_ok


def check_routes() -> bool:
    print("\n=== Checking API routes ===")
    try:
        from knowledge_engine.apps.api.main import app, create_app

        # Collect all route paths. In newer FastAPI, include_router() wraps
        # routes in _IncludedRouter objects with an include_context.
        route_paths: list[str] = []
        for r in app.router.routes:
            if hasattr(r, "path") and r.path:
                route_paths.append(str(r.path))
            elif hasattr(r, "include_context"):
                ctx = r.include_context
                router = ctx.included_router if hasattr(ctx, "included_router") else None
                if router and hasattr(router, "routes"):
                    for inner in router.routes:
                        if hasattr(inner, "path") and inner.path:
                            route_paths.append(str(inner.path))

        route_paths = sorted(set(route_paths))
        print(f"  App routes ({len(route_paths)}):")
        for path in route_paths:
            print(f"    {path}")

        expected = ["/", "/health", "/v1/capabilities", "/v1/validate"]
        for ep in expected:
            status = "OK" if ep in route_paths else "MISSING"
            print(f"  {ep}: {status}")
        return all(ep in route_paths for ep in expected)
    except Exception as exc:
        print(f"  ERROR importing app: {exc}")
        return False


def check_service_untouched() -> bool:
    print("\n=== Checking service.py is untouched ===")
    import hashlib
    expected_hash = "a9b97a338de958d416df0c879a40b576"
    with open("knowledge_engine/apps/api/service.py", "rb") as f:
        actual = hashlib.md5(f.read()).hexdigest()
    status = "OK" if actual == expected_hash else "CHANGED!"
    print(f"  service.py: {status}")
    return actual == expected_hash


def main() -> int:
    results = {
        "imports": check_imports(),
        "routes": check_routes(),
        "service": check_service_untouched(),
    }

    print("\n=== Summary ===")
    for name, ok in results.items():
        print(f"  {name}: {'PASS' if ok else 'FAIL'}")

    all_pass = all(results.values())
    print(f"\nOverall: {'ALL CHECKS PASSED' if all_pass else 'SOME CHECKS FAILED'}")
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
