from __future__ import annotations

from pathlib import Path

from fastapi import Cookie, FastAPI
from fastapi.responses import FileResponse

from app.config import get_settings
from app.api.routes import router


app = FastAPI(title="wechat-md-server", version="0.1.0")
app.include_router(router)


@app.get("/", include_in_schema=False)
async def index(
    access_cookie: str | None = Cookie(default=None, alias="wechat_md_access_token"),
) -> FileResponse:
    return _resolve_page("index.html", access_cookie)


@app.get("/settings", include_in_schema=False)
async def settings_page(
    access_cookie: str | None = Cookie(default=None, alias="wechat_md_access_token"),
) -> FileResponse:
    return _resolve_page("settings.html", access_cookie)


def _resolve_page(page_name: str, access_cookie: str | None) -> FileResponse:
    web_dir = Path(__file__).resolve().parent / "web"
    settings = get_settings()
    if settings.access_token and access_cookie != settings.access_token:
        return FileResponse(web_dir / "login.html")
    return FileResponse(web_dir / page_name)
