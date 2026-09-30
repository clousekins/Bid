from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.types import ASGIApp, Receive, Scope, Send

from app.api import apply as apply_api
from app.api import chatgpt as chatgpt_api
from app.api import jobs as jobs_api
from app.api import jobs_bg as jobs_bg_api
from app.api import manual_apply as manual_apply_api
from app.api import rejected as rejected_api
from app.api import settings as settings_api
from app.api import sync as sync_api
from app.config import FRONTEND_DIST, get_settings
from app.db.session import init_db
from app.services import bg_jobs

TOKEN_COOKIE = "poong_token"


class AccessTokenMiddleware:
    """Pure ASGI middleware (avoids BaseHTTPMiddleware quirks on Windows ASGI tests)."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        s = get_settings()
        path = scope.get("path") or ""
        if not s.access_token or path == "/api/health":
            await self.app(scope, receive, send)
            return

        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers") or []}
        token = None
        auth = headers.get("authorization") or ""
        if auth.lower().startswith("bearer "):
            token = auth[7:].strip()
        if not token:
            qs = (scope.get("query_string") or b"").decode()
            for part in qs.split("&"):
                if part.startswith("token="):
                    token = part.split("=", 1)[1]
                    break
        if not token:
            cookie = headers.get("cookie") or ""
            for part in cookie.split(";"):
                part = part.strip()
                if part.startswith(f"{TOKEN_COOKIE}="):
                    token = part.split("=", 1)[1]
                    break

        if token != s.access_token:
            if path.startswith("/api/") or path in {"/", "/index.html"} or not path.startswith(
                "/assets"
            ):
                response = JSONResponse(
                    {"detail": "unauthorized — open /?token=YOUR_ACCESS_TOKEN"},
                    status_code=401,
                )
                await response(scope, receive, send)
                return

        async def send_wrapper(message):
            if message["type"] == "http.response.start" and token == s.access_token:
                # set cookie if query token was used
                qs = (scope.get("query_string") or b"").decode()
                if f"token={s.access_token}" in qs:
                    headers_list = list(message.get("headers") or [])
                    cookie_val = (
                        f"{TOKEN_COOKIE}={s.access_token}; Path=/; Max-Age={60*60*24*30}; HttpOnly; SameSite=Lax"
                    )
                    headers_list.append((b"set-cookie", cookie_val.encode()))
                    message = {**message, "headers": headers_list}
            await send(message)

        await self.app(scope, receive, send_wrapper)


def create_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        await init_db()
        await bg_jobs.heal_stuck_jobs(older_than_minutes=1)
        bg_jobs.ensure_worker()
        yield

    cfg = get_settings()
    app = FastAPI(title="POONG", version="1.0.0", lifespan=lifespan)

    origins = [o.strip() for o in cfg.cors_origins.split(",") if o.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins if origins != ["*"] else ["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(AccessTokenMiddleware)

    @app.get("/api/health")
    async def health():
        return {"ok": True}

    app.include_router(jobs_api.router)
    app.include_router(sync_api.router)
    app.include_router(jobs_bg_api.router)
    app.include_router(apply_api.router)
    app.include_router(manual_apply_api.router)
    app.include_router(settings_api.router)
    app.include_router(rejected_api.router)
    app.include_router(chatgpt_api.router)

    dist = FRONTEND_DIST
    if dist.exists():
        assets = dist / "assets"
        if assets.exists():
            app.mount("/assets", StaticFiles(directory=assets), name="assets")

        @app.get("/{full_path:path}")
        async def spa(full_path: str = ""):
            index = dist / "index.html"
            candidate = dist / full_path
            if full_path and candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(index)

    return app


app = create_app()
