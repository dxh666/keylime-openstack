"""FastAPI entrypoint."""

from __future__ import annotations

import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from keylime_openstack.api.router import router
from keylime_openstack.config import get_settings


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Keylime OpenStack Trust Plane",
        version="0.2.0",
        description="Production-oriented Keylime + OpenStack trusted compute API.",
    )
    app.include_router(router)
    if settings.frontend_path.exists():
        app.mount("/", StaticFiles(directory=str(settings.frontend_path), html=True), name="frontend")
    return app


app = create_app()


def run() -> None:
    uvicorn.run("keylime_openstack.main:app", host="0.0.0.0", port=8088, reload=False)


if __name__ == "__main__":
    run()
