"""FastAPI entrypoint."""

from __future__ import annotations

import logging

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.responses import Response
from fastapi.staticfiles import StaticFiles

from keylime_openstack.api.router import router
from keylime_openstack.config import get_settings

LOG = logging.getLogger(__name__)


class NoCacheStaticFiles(StaticFiles):
    async def get_response(self, path: str, scope) -> Response:  # type: ignore[override]
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-store"
        return response


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Keylime OpenStack Trust Plane",
        version="0.2.0",
        description="Production-oriented Keylime + OpenStack trusted compute API.",
    )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(_: Request, exc: Exception) -> JSONResponse:
        LOG.exception("unhandled API exception")
        return JSONResponse(
            status_code=500,
            content={"detail": "服务器内部错误，请查看 API 日志。", "error": str(exc)},
        )

    app.include_router(router)
    if settings.frontend_path.exists():
        app.mount("/", NoCacheStaticFiles(directory=str(settings.frontend_path), html=True), name="frontend")
    return app


app = create_app()


def run() -> None:
    uvicorn.run("keylime_openstack.main:app", host="0.0.0.0", port=8088, reload=False)


if __name__ == "__main__":
    run()
