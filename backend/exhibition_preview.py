"""Start the explicitly isolated local Exhibition + Passport preview server."""

from __future__ import annotations

import os

import uvicorn

_PRODUCTION_LIKE_ENVIRONMENTS = {"production", "prod", "staging", "stage"}


def configure_local_preview_environment() -> None:
    """Enable the in-memory preview only from a non-production environment."""
    configured_environments = {
        name: (os.getenv(name) or "").strip().lower()
        for name in ("APP_ENV", "ENVIRONMENT")
    }
    blocked = {
        name: value
        for name, value in configured_environments.items()
        if value in _PRODUCTION_LIKE_ENVIRONMENTS
    }
    if blocked:
        details = ", ".join(f"{name}={value}" for name, value in blocked.items())
        raise RuntimeError(
            "The local Exhibition preview cannot run in a production-like environment: " + details
        )

    # These process-only settings force backend.main to choose the in-memory
    # preview stores, even when a local env file contains Supabase settings.
    os.environ["ENABLE_EXHIBITION_LOCAL_PREVIEW"] = "1"
    os.environ["APP_ENV"] = "local"
    # Local-only staff authentication for the isolated in-memory preview.
    # Production-like environments are rejected above, and the server binds
    # to loopback below.
    os.environ["ALLOW_INSECURE_TEST_AUTH"] = "1"
    os.environ["SHAKER_OPS_USER_IDS"] = "local-preview-ops"
    os.environ.pop("ENABLE_EXHIBITION_PREP", None)
    os.environ.pop("ENABLE_EXHIBITION_PASSPORT", None)


def main() -> None:
    configure_local_preview_environment()
    uvicorn.run(
        "backend.main:app",
        host="127.0.0.1",
        port=int(os.getenv("PORT", "8000")),
        reload=True,
    )


if __name__ == "__main__":
    main()
