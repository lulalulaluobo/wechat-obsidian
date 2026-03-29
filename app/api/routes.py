from __future__ import annotations

import threading
import time
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
    update_feishu_webhook_state,
    update_password,
    update_telegram_webhook_state,
)
from app.services import (
    build_config_payload,
    build_output_target,
    check_fns_status,
    configure_feishu_webhook_state,
    configure_telegram_webhook,
    execute_single_conversion,
    extract_feishu_message_text,
    extract_single_wechat_url,
    ensure_runtime_environment,
    get_internal_workdir_root,
    job_store,
    normalize_output_dir,
    parse_links,
    read_uploaded_text,
    send_feishu_message,
    send_telegram_message,
    submit_feishu_convert_task,
    submit_telegram_convert_task,
    test_ai_connectivity,
    TELEGRAM_SECRET_HEADER,
)


router = APIRouter()
_BOT_EVENT_TTL_SECONDS = 10 * 60
_bot_event_cache: dict[str, float] = {}
_bot_event_lock = threading.Lock()


def _remember_bot_event(key: str | None, platform: str) -> bool:
    if not key:
        return False
    now = time.monotonic()
    with _bot_event_lock:
        expired = [event_key for event_key, expires_at in _bot_event_cache.items() if expires_at <= now]
        for event_key in expired:
            _bot_event_cache.pop(event_key, None)
        if key in _bot_event_cache:
            print(f"[bot] duplicate message ignored platform={platform} key={key}")
            return True
        _bot_event_cache[key] = now + _BOT_EVENT_TTL_SECONDS
    return False


def _build_telegram_event_key(payload: dict[str, Any], chat_id: str) -> str | None:
    message = payload.get("message") if isinstance(payload.get("message"), dict) else {}
    message_id = str(message.get("message_id") or "").strip()
    if message_id:
        return f"telegram:{chat_id}:{message_id}"
    update_id = str(payload.get("update_id") or "").strip()
    if update_id:
        return f"telegram:update:{update_id}"
    return None


def _build_feishu_event_key(payload: dict[str, Any], open_id: str) -> str | None:
    header = payload.get("header") if isinstance(payload.get("header"), dict) else {}
    event = payload.get("event") if isinstance(payload.get("event"), dict) else {}
    message = event.get("message") if isinstance(event.get("message"), dict) else {}
    event_id = str(header.get("event_id") or "").strip()
    if event_id:
        return f"feishu:{event_id}"
    message_id = str(message.get("message_id") or "").strip()
    if message_id:
        return f"feishu:{open_id}:{message_id}"
    return None


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
        feishu_webhook_state = configure_feishu_webhook_state()
        if isinstance(webhook_state, dict):
            update_telegram_webhook_state(
                str(webhook_state.get("status") or "inactive"),
                str(webhook_state.get("message") or ""),
                webhook_url=str(webhook_state.get("webhook_url") or ""),
            )
        if isinstance(feishu_webhook_state, dict):
            update_feishu_webhook_state(
                str(feishu_webhook_state.get("status") or "inactive"),
                str(feishu_webhook_state.get("message") or ""),
                webhook_url=str(feishu_webhook_state.get("webhook_url") or ""),
            )
    except Exception as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    return {
        "status": "success",
        "settings": build_admin_settings_payload(),
        "telegram_webhook": webhook_state,
        "feishu_webhook": feishu_webhook_state,
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
    event_key = _build_telegram_event_key(payload, chat_id)
    if _remember_bot_event(event_key, "telegram"):
        return {"status": "ignored", "reason": "duplicate_message"}

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


@router.post("/api/integrations/feishu/webhook")
async def feishu_webhook(
    request: Request,
) -> dict[str, Any]:
    settings = get_settings()
    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="飞书 webhook payload 无效")

    if payload.get("type") == "url_verification" or "encrypt" in payload:
        print(f"[feishu] webhook payload={_sanitize_feishu_debug_payload(payload)}")

    event_type = str(payload.get("type") or "").strip()
    if event_type == "url_verification":
        token = str(payload.get("token") or "").strip()
        if not settings.feishu_verification_token or token != settings.feishu_verification_token:
            raise HTTPException(status_code=403, detail="飞书 verification token 无效")
        return {"challenge": str(payload.get("challenge") or "")}

    if not settings.feishu_enabled:
        return {"status": "ignored", "reason": "feishu_disabled"}

    text, open_id, chat_type = extract_feishu_message_text(payload)
    if not open_id:
        return {"status": "ignored", "reason": "missing_open_id"}
    print(f"[feishu] received message open_id={open_id} chat_type={chat_type}")
    if chat_type != "p2p":
        return {"status": "ignored", "reason": "chat_type_not_supported"}
    if settings.feishu_allowed_open_ids and open_id not in settings.feishu_allowed_open_ids:
        return {"status": "ignored", "reason": "open_id_not_allowed"}
    event_key = _build_feishu_event_key(payload, open_id)
    if _remember_bot_event(event_key, "feishu"):
        return {"status": "ignored", "reason": "duplicate_message"}

    url, url_count = extract_single_wechat_url(text)
    print(
        "[feishu] message parsed "
        f"open_id={open_id} url_count={url_count} "
        f"url_found={bool(url)} event_key={event_key or '-'}"
    )
    if url_count == 0 or not url:
        _safe_send_feishu_message(open_id, "未识别到可用的微信文章链接，请直接发送一条公众号文章链接。")
        return {"status": "replied", "reason": "no_link"}
    if url_count > 1:
        _safe_send_feishu_message(open_id, "一次只支持一篇文章，请只发送一条微信文章链接。")
        return {"status": "replied", "reason": "multiple_links"}
    if not settings.fns_enabled:
        _safe_send_feishu_message(open_id, "当前 FNS 尚未配置完成，无法执行飞书单篇转换。")
        return {"status": "replied", "reason": "fns_not_configured"}

    _safe_send_feishu_message(open_id, "已接收，开始转换。")
    submit_feishu_convert_task(url, open_id)
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


def _safe_send_feishu_message(open_id: str, text: str) -> None:
    try:
        send_feishu_message(open_id, text)
    except Exception as error:
        print(f"[feishu] send message failed open_id={open_id}: {error}")


def _sanitize_feishu_debug_payload(payload: dict[str, Any]) -> dict[str, Any]:
    sanitized: dict[str, Any] = {}
    for key, value in payload.items():
        if key == "token":
            sanitized[key] = "***"
        elif key == "encrypt":
            encrypted = str(value or "")
            sanitized[key] = {"present": bool(encrypted), "length": len(encrypted)}
        elif key == "challenge":
            sanitized[key] = str(value or "")
        else:
            sanitized[key] = value
    return sanitized
