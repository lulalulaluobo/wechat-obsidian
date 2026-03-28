from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Cookie, File, Header, HTTPException, Request, Response, UploadFile

from app.config import build_admin_settings_payload, get_settings, save_runtime_config
from app.core.pipeline import run_pipeline
from app.services import (
    build_output_target,
    build_config_payload,
    check_fns_status,
    ensure_runtime_environment,
    job_store,
    normalize_output_dir,
    parse_links,
    read_uploaded_text,
    sync_result_to_output,
)


router = APIRouter()


@router.get("/api/config")
async def get_config(
    authorization: str | None = Header(default=None),
    access_cookie: str | None = Cookie(default=None, alias="wechat_md_access_token"),
) -> dict[str, Any]:
    _require_access(authorization, access_cookie)
    return build_config_payload()


@router.post("/api/convert")
async def convert_article(
    request: Request,
    authorization: str | None = Header(default=None),
    access_cookie: str | None = Cookie(default=None, alias="wechat_md_access_token"),
) -> dict[str, Any]:
    _require_access(authorization, access_cookie)
    payload = await _read_convert_payload(request)
    url = payload.get("url", "").strip()
    if not url:
        raise HTTPException(status_code=400, detail="缺少微信文章链接 url")

    output_dir = normalize_output_dir(payload.get("output_dir"))
    timeout = int(payload.get("timeout") or get_settings().default_timeout)
    save_html = _parse_bool(payload.get("save_html"))
    output_target = build_output_target(payload.get("output_target"))
    ensure_runtime_environment()

    try:
        result = run_pipeline(url=url, output_base_dir=output_dir, save_html=save_html, timeout=timeout)
        sync = sync_result_to_output(result, output_target=output_target)
    except Exception as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    return {"status": "success", "output_target": output_target, "result": result, "sync": sync}


@router.post("/api/batch")
async def convert_batch(
    request: Request,
    file: UploadFile | None = File(default=None),
    authorization: str | None = Header(default=None),
    access_cookie: str | None = Cookie(default=None, alias="wechat_md_access_token"),
) -> dict[str, Any]:
    _require_access(authorization, access_cookie)
    payload = await _read_batch_payload(request, file=file)
    urls = parse_links(
        urls=payload.get("urls"),
        urls_text=payload.get("urls_text"),
        file_text=payload.get("file_text"),
    )
    if not urls:
        raise HTTPException(status_code=400, detail="未解析到可用的微信文章链接")

    output_dir = normalize_output_dir(payload.get("output_dir"))
    timeout = int(payload.get("timeout") or get_settings().default_timeout)
    save_html = _parse_bool(payload.get("save_html"))
    output_target = build_output_target(payload.get("output_target"))

    job = job_store.create_batch_job(
        urls=urls,
        output_dir=output_dir,
        save_html=save_html,
        timeout=timeout,
        output_target=output_target,
    )
    return {
        "status": "queued",
        "job_id": job["job_id"],
        "total": job["total"],
        "deduped_count": len(urls),
        "output_dir": job["output_dir"],
        "output_target": output_target,
    }


@router.get("/api/jobs/{job_id}")
async def get_job(
    job_id: str,
    authorization: str | None = Header(default=None),
    access_cookie: str | None = Cookie(default=None, alias="wechat_md_access_token"),
) -> dict[str, Any]:
    _require_access(authorization, access_cookie)
    job = job_store.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return job


@router.post("/api/session")
async def create_session(request: Request, response: Response) -> dict[str, Any]:
    settings = get_settings()
    if not settings.access_token:
        return {"status": "disabled", "auth_enabled": False}

    payload = await _read_convert_payload(request)
    token = str(payload.get("access_token") or "").strip()
    if token != settings.access_token:
        raise HTTPException(status_code=401, detail="访问令牌无效")

    response.set_cookie(
        "wechat_md_access_token",
        token,
        httponly=True,
        samesite="strict",
        max_age=7 * 24 * 60 * 60,
    )
    return {"status": "ok", "auth_enabled": True}


@router.delete("/api/session")
async def delete_session(response: Response) -> dict[str, Any]:
    response.delete_cookie("wechat_md_access_token")
    return {"status": "ok"}


@router.get("/api/admin/settings")
async def get_admin_settings(
    authorization: str | None = Header(default=None),
    access_cookie: str | None = Cookie(default=None, alias="wechat_md_access_token"),
) -> dict[str, Any]:
    _require_access(authorization, access_cookie)
    return build_admin_settings_payload()


@router.get("/api/admin/fns-status")
async def get_admin_fns_status(
    authorization: str | None = Header(default=None),
    access_cookie: str | None = Cookie(default=None, alias="wechat_md_access_token"),
) -> dict[str, Any]:
    _require_access(authorization, access_cookie)
    return check_fns_status()


@router.put("/api/admin/settings")
async def update_admin_settings(
    request: Request,
    response: Response,
    authorization: str | None = Header(default=None),
    access_cookie: str | None = Cookie(default=None, alias="wechat_md_access_token"),
) -> dict[str, Any]:
    _require_access(authorization, access_cookie)
    payload = await _read_convert_payload(request)
    clear_fields = payload.get("clear_fields")
    if not isinstance(clear_fields, list):
        clear_fields = []
    try:
        saved = save_runtime_config(payload, clear_fields=clear_fields)
    except Exception as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    settings = get_settings()
    session_invalidated = False
    if "access_token" in saved and settings.access_token != access_cookie:
        session_invalidated = True
        response.delete_cookie("wechat_md_access_token")

    return {
        "status": "success",
        "session_invalidated": session_invalidated,
        "settings": build_admin_settings_payload(),
    }


def _parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


async def _read_convert_payload(request: Request) -> dict[str, Any]:
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        return dict(await request.json())
    if "multipart/form-data" in content_type or "application/x-www-form-urlencoded" in content_type:
        form = await request.form()
        return {key: value for key, value in form.items()}
    return {}


async def _read_batch_payload(request: Request, file: UploadFile | None) -> dict[str, Any]:
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        payload = dict(await request.json())
        payload["file_text"] = ""
        return payload

    form = await request.form()
    payload = {key: value for key, value in form.items() if key != "file"}
    payload["urls"] = []
    if file is not None:
        payload["file_text"] = read_uploaded_text(await file.read())
    else:
        payload["file_text"] = ""
    return payload


def _require_access(authorization: str | None, access_cookie: str | None) -> None:
    settings = get_settings()
    if not settings.access_token:
        return

    bearer = ""
    if authorization and authorization.lower().startswith("bearer "):
        bearer = authorization[7:].strip()
    if bearer == settings.access_token or access_cookie == settings.access_token:
        return
    raise HTTPException(status_code=401, detail="未授权访问")
