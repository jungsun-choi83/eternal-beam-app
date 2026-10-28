"""Start the explicitly isolated local Exhibition + Passport preview server."""

from __future__ import annotations

import os

import uvicorn


def main() -> None:
    # These process-only settings force backend.main to choose the in-memory
    # preview stores, even when a local env file contains Supabase settings.
    os.environ["ENABLE_EXHIBITION_LOCAL_PREVIEW"] = "1"
    os.environ["APP_ENV"] = "local"
    os.environ.pop("ENABLE_EXHIBITION_PREP", None)
    os.environ.pop("ENABLE_EXHIBITION_PASSPORT", None)
    uvicorn.run(
        "backend.main:app",
        host="127.0.0.1",
        port=int(os.getenv("PORT", "8000")),
        reload=True,
    )


if __name__ == "__main__":
    main()
