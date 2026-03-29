from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Cookie, File, Header, HTTPException, Request, Response, UploadFile

from app.auth import (
    SESSION_COOKIE_NAME,
    build_session_token,
    check_login_allowed,
    clear_login_failures,
    record_login_failure,
    session_cookie_secure_enabled,
    verify_password,
    verify_session_token,
)
from app.config import (
    build_admin_settings_payload,
    get_settings,
    save_runtime_config,
    update_ai_selected_model,
    update_password,
    update_telegram_webhook_state,
)
from app.services import (
    build_config_payload,
    build_output_target,
    check_fns_status,
    configure_telegram_webhook,
    execute_single_conversion,
    extract_single_wechat_url,
    ensure_runtime_environment,
    get_internal_workdir_root,
    job_store,
    normalize_output_dir,
    parse_links,
    read_uploaded_text,
    send_telegram_message,
    submit_telegram_convert_task,
    test_ai_connectivity,
    TELEGRAM_SECRET_HEADER,
)


router = APIRouter()


@router.get("/api/config")
async def get_config(
    session_cookie: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
) -> dict[str, Any]:
    _require_access(session_cookie)
    return build_config_payload()


@router.post("/api/convert")
async def convert_article(
    request: Request,
    session_cookie: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
) -> dict[str, Any]:
    _require_access(session_cookie)
    payload = await _read_convert_payload(request)
    url = str(payload.get("url") or "").strip()
    if not url:
        raise HTTPException(status_code=400, detail="缺少微信文章链接 url")

    try:
        return execute_single_conversion(
            url=url,
            timeout=int(payload.get("timeout") or get_settings().default_timeout),
            save_html=_parse_bool(payload.get("save_html")),
            output_target=payload.get("output_target"),
            ai_enabled=_read_optional_bool(payload.get("ai_enabled")),
        )
    except Exception as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.post("/api/batch")
async def convert_batch(
    request: Request,
    file: UploadFile | None = File(default=None),
    session_cookie: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
) -> dict[str, Any]:
    _require_access(session_cookie)
    payload = await _read_batch_payload(request, file=file)
    urls = parse_links(
        urls=payload.get("urls"),
        urls_text=payload.get("urls_text"),
        file_text=payload.get("file_text"),
    )
    if not urls:
        raise HTTPException(status_code=400, detail="未解析到可用的微信文章链接")

    timeout = int(payload.get("timeout") or get_settings().default_timeout)
    save_html = _parse_bool(payload.get("save_html"))
    output_target = build_output_target(payload.get("output_target"))
    output_dir = get_internal_workdir_root() if output_target == "fns" else normalize_output_dir(payload.get("output_dir"))

    job = job_store.create_batch_job(
        urls=urls,
        output_dir=output_dir,
        save_html=save_html,
        timeout=timeout,
        output_target=output_target,
        ai_enabled=_read_optional_bool(payload.get("ai_enabled")),
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
    session_cookie: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
) -> dict[str, Any]:
    _require_access(session_cookie)
    job = job_store.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return job


@router.post("/api/session")
async def create_session(request: Request, response: Response) -> dict[str, Any]:
    payload = await _read_convert_payload(request)
    username = str(payload.get("username") or "").strip()
    password = str(payload.get("password") or "")
    settings = get_settings()
    client_host = request.client.host if request.client else "unknown"
    throttle_key = f"{client_host}:{username}"

    allowed, retry_after = check_login_allowed(throttle_key)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail="登录失败次数过多，请稍后再试",
            headers={"Retry-After": str(retry_after or 60)},
        )

    if username != settings.username or not verify_password(password, settings.password_hash):
        still_allowed, retry_after = record_login_failure(throttle_key)
        if not still_allowed:
            raise HTTPException(
                status_code=429,
                detail="登录失败次数过多，请稍后再试",
                headers={"Retry-After": str(retry_after or 60)},
            )
        raise HTTPException(status_code=401, detail="用户名或密码错误")

    clear_login_failures(throttle_key)

    response.set_cookie(
        SESSION_COOKIE_NAME,
        build_session_token(settings.username, settings.password_hash, settings.session_secret),
        httponly=True,
        samesite="strict",
        secure=session_cookie_secure_enabled(),
        max_age=7 * 24 * 60 * 60,
    )
    return {"status": "ok", "auth_enabled": True, "username": settings.username}


@router.delete("/api/session")
async def delete_session(response: Response) -> dict[str, Any]:
    response.delete_cookie(SESSION_COOKIE_NAME, samesite="strict", secure=session_cookie_secure_enabled())
    return {"status": "ok"}


@router.get("/api/admin/settings")
async def get_admin_settings(
    session_cookie: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
) -> dict[str, Any]:
    _require_access(session_cookie)
    return build_admin_settings_payload()


@router.get("/api/admin/fns-status")
async def get_admin_fns_status(
    session_cookie: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
) -> dict[str, Any]:
    _require_access(session_cookie)
    return check_fns_status()


@router.post("/api/admin/ai-test")
async def post_admin_ai_test(
    request: Request,
    session_cookie: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
) -> dict[str, Any]:
    _require_access(session_cookie)
    payload = await _read_convert_payload(request)
    settings = get_settings()
    provider_payload = payload.get("provider") if isinstance(payload.get("provider"), dict) else None
    model_payload = payload.get("model") if isinstance(payload.get("model"), dict) else None
    if provider_payload is None and model_payload is None:
        provider_payload = dict(settings.ai_selected_provider or {})
        model_payload = dict(settings.ai_selected_model or {})
    elif provider_payload is None or model_payload is None:
        raise HTTPException(status_code=400, detail="AI 测试需要同时提供 provider 和 model")
    else:
        saved_provider = dict(settings.ai_selected_provider or {})
        saved_model = dict(settings.ai_selected_model or {})
        if (
            saved_provider
            and str(provider_payload.get("id") or "").strip() == str(saved_provider.get("id") or "").strip()
            and not str(provider_payload.get("api_key") or "").strip()
        ):
            provider_payload = {**saved_provider, **provider_payload, "api_key": str(saved_provider.get("api_key") or "")}
        if (
            saved_model
            and str(model_payload.get("id") or "").strip() == str(saved_model.get("id") or "").strip()
        ):
            model_payload = {**saved_model, **model_payload}
    try:
        return test_ai_connectivity(
            provider=provider_payload,
            model=model_payload,
            timeout=int(payload.get("timeout") or settings.default_timeout),
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.post("/api/admin/ai-selection")
async def update_admin_ai_selection(
    request: Request,
    session_cookie: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
) -> dict[str, Any]:
    _require_access(session_cookie)
    payload = await _read_convert_payload(request)
    try:
        update_ai_selected_model(str(payload.get("ai_selected_model_id") or ""))
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return build_admin_settings_payload()


@router.put("/api/admin/settings")
async def update_admin_settings(
    request: Request,
    session_cookie: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
) -> dict[str, Any]:
    _require_access(session_cookie)
    payload = await _read_convert_payload(request)
    clear_fields = payload.get("clear_fields")
    if not isinstance(clear_fields, list):
        clear_fields = []
    try:
        save_runtime_config(payload, clear_fields=clear_fields)
        webhook_state = configure_telegram_webhook()
        if isinstance(webhook_state, dict):
            update_telegram_webhook_state(
                str(webhook_state.get("status") or "inactive"),
                str(webhook_state.get("message") or ""),
                webhook_url=str(webhook_state.get("webhook_url") or ""),
            )
    except Exception as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    return {
        "status": "success",
        "settings": build_admin_settings_payload(),
        "telegram_webhook": webhook_state,
    }


@router.put("/api/admin/password")
async def update_admin_password(
    request: Request,
    response: Response,
    session_cookie: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
) -> dict[str, Any]:
    _require_access(session_cookie)
    payload = await _read_convert_payload(request)
    current_password = str(payload.get("current_password") or "")
    new_password = str(payload.get("new_password") or "")
    try:
        updated = update_password(current_password=current_password, new_password=new_password)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    auth_user = updated["auth"]["user"]
    session_secret = str(updated["auth"]["session_secret"])
    response.set_cookie(
        SESSION_COOKIE_NAME,
        build_session_token(
            str(auth_user["username"]),
            str(auth_user["password_hash"]),
            session_secret,
        ),
        httponly=True,
        samesite="strict",
        secure=session_cookie_secure_enabled(),
        max_age=7 * 24 * 60 * 60,
    )
    return {"status": "success"}


@router.post("/api/integrations/telegram/webhook")
async def telegram_webhook(
    request: Request,
    telegram_secret: str | None = Header(default=None, alias=TELEGRAM_SECRET_HEADER),
) -> dict[str, Any]:
    settings = get_settings()
    if not settings.telegram_enabled:
        return {"status": "ignored", "reason": "telegram_disabled"}
    if not settings.telegram_webhook_secret or telegram_secret != settings.telegram_webhook_secret:
        raise HTTPException(status_code=403, detail="Telegram webhook secret 无效")

    payload = await request.json()
    message = payload.get("message") if isinstance(payload, dict) else None
    if not isinstance(message, dict):
        return {"status": "ignored", "reason": "no_message"}

    chat = message.get("chat") if isinstance(message.get("chat"), dict) else {}
    chat_id = str(chat.get("id") or "").strip()
    if not chat_id or chat_id not in settings.telegram_allowed_chat_ids:
        return {"status": "ignored", "reason": "chat_not_allowed"}

    text = str(message.get("text") or "").strip()
    url, url_count = extract_single_wechat_url(text)
    if url_count == 0 or not url:
        send_telegram_message(chat_id, "未识别到可用的微信文章链接，请直接发送一条公众号文章链接。")
        return {"status": "replied", "reason": "no_link"}
    if url_count > 1:
        send_telegram_message(chat_id, "一次只支持一篇文章，请只发送一条微信文章链接。")
        return {"status": "replied", "reason": "multiple_links"}
    if not settings.fns_enabled:
        send_telegram_message(chat_id, "当前 FNS 尚未配置完成，无法执行 Telegram 单篇转换。")
        return {"status": "replied", "reason": "fns_not_configured"}

    send_telegram_message(chat_id, "已接收，开始转换。")
    submit_telegram_convert_task(url, chat_id)
    return {"status": "accepted"}


def _parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _read_optional_bool(value: Any) -> bool | None:
    if value is None or value == "":
        return None
    return _parse_bool(value)


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


def _require_access(session_cookie: str | None) -> None:
    settings = get_settings()
    if verify_session_token(session_cookie, settings.username, settings.password_hash, settings.session_secret):
        return
    raise HTTPException(status_code=401, detail="未授权访问")


def is_authenticated(session_cookie: str | None) -> bool:
    settings = get_settings()
    return verify_session_token(session_cookie, settings.username, settings.password_hash, settings.session_secret)
