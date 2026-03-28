from __future__ import annotations

from pathlib import Path

from fastapi import Cookie, FastAPI
from fastapi.responses import FileResponse, RedirectResponse

from app.api.routes import is_authenticated, router
from app.auth import SESSION_COOKIE_NAME


app = FastAPI(title="wechat-md-server", version="0.1.0")
app.include_router(router)


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
    web_dir = Path(__file__).resolve().parent / "web"
    if is_authenticated(session_cookie):
        return RedirectResponse(url="/", status_code=303)
    return FileResponse(web_dir / "login.html")


def _resolve_page(page_name: str, session_cookie: str | None):
    web_dir = Path(__file__).resolve().parent / "web"
    if not is_authenticated(session_cookie):
        return RedirectResponse(url="/login", status_code=303)
    return FileResponse(web_dir / page_name)
