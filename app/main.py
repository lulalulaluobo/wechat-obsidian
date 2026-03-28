from __future__ import annotations

from pathlib import Path

from fastapi import Cookie, FastAPI, Request
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import is_authenticated, router
from app.auth import SESSION_COOKIE_NAME


app = FastAPI(title="wechat-md-server", version="0.1.0")
app.include_router(router)
WEB_DIR = Path(__file__).resolve().parent / "web"
app.mount("/assets", StaticFiles(directory=WEB_DIR / "assets"), name="assets")


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    return response


@app.get("/", include_in_schema=False)
async def index(
    session_cookie: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
):
    return _resolve_page("index.html", session_cookie)


@app.get("/settings", include_in_schema=False)
async def settings_page(
    session_cookie: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
):
    return _resolve_page("settings.html", session_cookie)


@app.get("/login", include_in_schema=False)
async def login_page(
    session_cookie: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
):
    if is_authenticated(session_cookie):
        return RedirectResponse(url="/", status_code=303)
    return FileResponse(WEB_DIR / "login.html")


def _resolve_page(page_name: str, session_cookie: str | None):
    if not is_authenticated(session_cookie):
        return RedirectResponse(url="/login", status_code=303)
    return FileResponse(WEB_DIR / page_name)
