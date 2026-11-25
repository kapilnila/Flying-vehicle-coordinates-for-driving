"""Start the combined Render web service."""
import os

import uvicorn


def main():
    database_url = os.getenv("DATABASE_URL", "")
    if not database_url.startswith(("postgres://", "postgresql://")):
        raise SystemExit("Set DATABASE_URL to your existing PostgreSQL connection URL.")
    if not os.getenv("FRONTEND_DIST"):
        raise SystemExit("Set FRONTEND_DIST to the built dashboard directory.")
    if not os.getenv("API_TOKEN") and os.getenv("PUBLIC_API", "").strip().lower() != "true":
        raise SystemExit("Set API_TOKEN before starting the hosted service.")
    uvicorn.run("spatial_id.api:create_app", factory=True, host="0.0.0.0",
                port=int(os.getenv("PORT", "8080")), timeout_graceful_shutdown=10,
                limit_concurrency=64)


if __name__ == "__main__":
    main()
