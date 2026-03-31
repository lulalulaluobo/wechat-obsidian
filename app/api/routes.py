from __future__ import annotations

import secrets
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
    authenticate_db_user,
    build_sync_config_payload,
    build_config_payload,
    build_output_target,
    check_fns_status,
    check_wechat_mp_login_status,
    change_db_user_password,
    confirm_wechat_mp_qr_login,
    configure_feishu_webhook_state,
    configure_telegram_webhook,
    create_sync_source,
    delete_sync_articles,
    delete_sync_source,
    ensure_admin_user_bootstrap,
    execute_single_conversion,
    extract_feishu_message_text,
    extract_single_wechat_url,
    get_db_user,
    get_scheduler_settings,
    get_wechat_mp_credentials,
    get_wechat_mp_qr_login_status,
    ensure_runtime_environment,
    get_ingest_job,
    get_internal_workdir_root,
    get_task,
    job_store,
    list_article_execution_history,
    list_tasks,
    list_sync_articles,
    list_sync_sources_payload,
    normalize_output_dir,
    parse_links,
    read_uploaded_text,
    search_wechat_accounts,
    send_feishu_message,
    send_telegram_message,
    save_wechat_mp_credentials,
    resolve_article_ids_from_selection,
    start_wechat_mp_qr_login,
    submit_article_ingest,
    submit_feishu_convert_task,
    submit_rerun_task,
    submit_rerun_tasks,
    submit_telegram_convert_task,
    sync_source_articles,
    test_ai_connectivity,
    TELEGRAM_SECRET_HEADER,
    update_scheduler_settings,
)
router = APIRouter()
CSRF_COOKIE_NAME = "wechat_md_csrf"
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
        raise HTTPException(status_code=400, detail="缺少文章链接 url")

    try:
        return execute_single_conversion(
            url=url,
            timeout=int(payload.get("timeout") or get_settings().default_timeout),
            save_html=_parse_bool(payload.get("save_html")),
            output_target=payload.get("output_target"),
            ai_enabled=_read_optional_bool(payload.get("ai_enabled")),
            require_ai_success=True,
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
        raise HTTPException(status_code=400, detail="未解析到可用的文章链接")

    timeout = int(payload.get("timeout") or get_settings().default_timeout)
    save_html = _parse_bool(payload.get("save_html"))
    output_target = build_output_target(payload.get("output_target"))
    output_dir = get_internal_workdir_root() if output_target == "fns" else normalize_output_dir(payload.get("output_dir"))

    task_items = [{"url": url, "task_id": ""} for url in urls]

    job = job_store.create_batch_job(
        urls=urls,
        output_dir=output_dir,
        save_html=save_html,
        timeout=timeout,
        output_target=output_target,
        ai_enabled=_read_optional_bool(payload.get("ai_enabled")),
        require_ai_success=True,
        task_items=task_items,
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


@router.get("/api/tasks")
async def get_tasks(
    trigger_channel: str | None = None,
    source_type: str | None = None,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
    session_cookie: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
) -> dict[str, Any]:
    _require_access(session_cookie)
    try:
        return list_tasks(
            trigger_channel=trigger_channel,
            source_type=source_type,
            status=status,
            limit=limit,
            offset=offset,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.post("/api/tasks/{task_id}/rerun")
async def rerun_task(
    task_id: str,
    session_cookie: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
) -> dict[str, Any]:
    _require_access(session_cookie)
    if get_task(task_id) is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    try:
        payload = submit_rerun_task(task_id)
    except (KeyError, ValueError) as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return {"status": "accepted", **payload}


@router.post("/api/tasks/rerun")
async def rerun_tasks(
    request: Request,
    session_cookie: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
) -> dict[str, Any]:
    _require_access(session_cookie)
    payload = await _read_convert_payload(request)
    task_ids = payload.get("task_ids")
    if not isinstance(task_ids, list) or not task_ids:
        raise HTTPException(status_code=400, detail="task_ids 不能为空")
    try:
        result = submit_rerun_tasks([str(item) for item in task_ids if str(item).strip()])
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return {"status": "accepted", **result}


@router.post("/api/session")
async def create_session(request: Request, response: Response) -> dict[str, Any]:
    payload = await _read_convert_payload(request)
    username = str(payload.get("username") or "").strip()
    password = str(payload.get("password") or "")
    ensure_admin_user_bootstrap()
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

    user = authenticate_db_user(username, password)
    if user is None:
        still_allowed, retry_after = record_login_failure(throttle_key)
        if not still_allowed:
            raise HTTPException(
                status_code=429,
                detail="登录失败次数过多，请稍后再试",
                headers={"Retry-After": str(retry_after or 60)},
            )
        raise HTTPException(status_code=401, detail="用户名或密码错误")

    clear_login_failures(throttle_key)
    csrf_token = secrets.token_urlsafe(24)

    response.set_cookie(
        SESSION_COOKIE_NAME,
        build_session_token(str(user.get("username") or ""), str(user.get("password_hash") or ""), settings.session_secret),
        httponly=True,
        samesite="strict",
        secure=session_cookie_secure_enabled(),
        max_age=7 * 24 * 60 * 60,
    )
    response.set_cookie(
        CSRF_COOKIE_NAME,
        csrf_token,
        httponly=False,
        samesite="strict",
        secure=session_cookie_secure_enabled(),
        max_age=7 * 24 * 60 * 60,
    )
    return {
        "status": "ok",
        "auth_enabled": True,
        "username": str(user.get("username") or ""),
        "role": str(user.get("role") or "operator"),
        "csrf_token": csrf_token,
    }


@router.delete("/api/session")
async def delete_session(response: Response) -> dict[str, Any]:
    response.delete_cookie(SESSION_COOKIE_NAME, samesite="strict", secure=session_cookie_secure_enabled())
    response.delete_cookie(CSRF_COOKIE_NAME, samesite="strict", secure=session_cookie_secure_enabled())
    return {"status": "ok"}

@router.get("/api/admin/schedules")
async def get_admin_schedules(
    session_cookie: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
) -> dict[str, Any]:
    _require_admin(session_cookie)
    return get_scheduler_settings()


@router.put("/api/admin/schedules")
async def put_admin_schedules(
    request: Request,
    session_cookie: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
) -> dict[str, Any]:
    actor = _require_admin(session_cookie)
    _require_csrf(request, strict=True)
    payload = await _read_convert_payload(request)
    return update_scheduler_settings(
        payload,
        actor_user_id=str(actor.get("id") or ""),
        ip_address=request.client.host if request.client else "",
    )


@router.get("/api/admin/settings")
async def get_admin_settings(
    session_cookie: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
) -> dict[str, Any]:
    user = _require_access(session_cookie)
    payload = build_admin_settings_payload()
    payload["current_user"] = {
        "id": str(user.get("id") or ""),
        "username": str(user.get("username") or ""),
        "role": str(user.get("role") or "operator"),
    }
    return payload


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
    _require_csrf(request)
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
    _require_csrf(request)
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
    user = _require_access(session_cookie)
    _require_csrf(request)
    payload = await _read_convert_payload(request)
    current_password = str(payload.get("current_password") or "")
    new_password = str(payload.get("new_password") or "")
    try:
        updated = change_db_user_password(
            str(user.get("username") or ""),
            current_password,
            new_password,
            actor_user_id=str(user.get("id") or ""),
            ip_address=request.client.host if request.client else "",
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error

    auth_user = updated["user"]
    session_secret = str(get_settings().session_secret)
    response.set_cookie(
        SESSION_COOKIE_NAME,
        build_session_token(
            str(auth_user.get("username") or ""),
            str(auth_user.get("password_hash") or ""),
            session_secret,
        ),
        httponly=True,
        samesite="strict",
        secure=session_cookie_secure_enabled(),
        max_age=7 * 24 * 60 * 60,
    )
    return {"status": "success"}


@router.get("/api/sync/config")
async def get_sync_config(
    session_cookie: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
) -> dict[str, Any]:
    _require_access(session_cookie)
    return build_sync_config_payload()


@router.put("/api/sync/config")
async def update_sync_config(
    request: Request,
    session_cookie: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
) -> dict[str, Any]:
    _require_access(session_cookie)
    _require_csrf(request)
    payload = await _read_convert_payload(request)
    clear_fields = payload.get("clear_fields")
    if not isinstance(clear_fields, list):
        clear_fields = []
    try:
        db_token = payload.get("wechat_mp_token")
        db_cookie = payload.get("wechat_mp_cookie")
        if db_token is not None or db_cookie is not None or "wechat_mp_token" in clear_fields or "wechat_mp_cookie" in clear_fields:
            current = get_wechat_mp_credentials()
            token = str(db_token or "") if db_token is not None else str(current.get("token") or "")
            cookie = str(db_cookie or "") if db_cookie is not None else str(current.get("cookie") or "")
            if "wechat_mp_token" in clear_fields:
                token = ""
            if "wechat_mp_cookie" in clear_fields:
                cookie = ""
            save_wechat_mp_credentials(token, cookie)
            payload = {key: value for key, value in payload.items() if key not in {"wechat_mp_token", "wechat_mp_cookie"}}
            clear_fields = [item for item in clear_fields if item not in {"wechat_mp_token", "wechat_mp_cookie"}]
        save_runtime_config(payload, clear_fields=clear_fields)
    except Exception as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return {"status": "success", "config": build_sync_config_payload()}


@router.get("/api/sync/login-status")
async def get_sync_login_status(
    session_cookie: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
) -> dict[str, Any]:
    _require_access(session_cookie)
    try:
        return check_wechat_mp_login_status()
    except Exception as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.get("/api/sync/search")
async def get_sync_search(
    keyword: str,
    begin: int = 0,
    size: int = 5,
    session_cookie: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
) -> dict[str, Any]:
    _require_access(session_cookie)
    try:
        return search_wechat_accounts(keyword=keyword, begin=begin, size=size)
    except Exception as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.get("/api/sync/sources")
async def get_sync_sources(
    session_cookie: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
) -> dict[str, Any]:
    _require_access(session_cookie)
    return list_sync_sources_payload()


@router.post("/api/sync/sources")
async def post_sync_source(
    request: Request,
    session_cookie: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
) -> dict[str, Any]:
    _require_access(session_cookie)
    _require_csrf(request)
    payload = await _read_convert_payload(request)
    try:
        return create_sync_source(payload)
    except Exception as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.delete("/api/sync/sources/{source_id}")
async def delete_sync_source_route(
    source_id: str,
    request: Request,
    session_cookie: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
) -> dict[str, Any]:
    _require_access(session_cookie)
    _require_csrf(request)
    delete_sync_source(source_id)
    return {"status": "success"}


@router.post("/api/sync/sources/{source_id}/sync")
async def post_sync_source_run(
    source_id: str,
    request: Request,
    session_cookie: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
) -> dict[str, Any]:
    _require_access(session_cookie)
    _require_csrf(request)
    payload = await _read_convert_payload(request)
    try:
        return sync_source_articles(
            source_id=source_id,
            start_date=str(payload.get("start_date") or "").strip() or None,
            end_date=str(payload.get("end_date") or "").strip() or None,
            output_target=str(payload.get("output_target") or build_output_target(None)).strip(),
            skip_ingested=_parse_bool(payload.get("skip_ingested")) if payload.get("skip_ingested") is not None else True,
        )
    except Exception as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.get("/api/sync/articles")
async def get_sync_articles(
    account_fakeid: str | None = None,
    source_id: str | None = None,
    sync_run_id: str | None = None,
    has_execution: bool | None = None,
    process_status: str | None = None,
    is_ingested: bool | None = None,
    published_from: int | None = None,
    published_to: int | None = None,
    limit: int = 100,
    offset: int = 0,
    session_cookie: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
) -> dict[str, Any]:
    _require_access(session_cookie)
    return list_sync_articles(
        account_fakeid=account_fakeid,
        source_id=source_id,
        sync_run_id=sync_run_id,
        has_execution=has_execution,
        process_status=process_status,
        is_ingested=is_ingested,
        published_from=published_from,
        published_to=published_to,
        limit=limit,
        offset=offset,
    )


@router.post("/api/sync/articles/delete")
async def post_sync_articles_delete(
    request: Request,
    session_cookie: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
) -> dict[str, Any]:
    actor = _require_access(session_cookie)
    _require_csrf(request, strict=True)
    payload = await _read_convert_payload(request)
    selection = payload.get("selection") if isinstance(payload.get("selection"), dict) else {}
    return delete_sync_articles(
        selection=selection,
        actor_user_id=str(actor.get("id") or ""),
        ip_address=request.client.host if request.client else "",
    )


@router.get("/api/sync/articles/{article_id}/executions")
async def get_sync_article_executions(
    article_id: str,
    limit: int = 50,
    offset: int = 0,
    session_cookie: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
) -> dict[str, Any]:
    _require_access(session_cookie)
    return list_article_execution_history(article_id, limit=limit, offset=offset)


@router.post("/api/sync/articles/ingest")
async def post_sync_articles_ingest(
    request: Request,
    session_cookie: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
) -> dict[str, Any]:
    _require_access(session_cookie)
    _require_csrf(request)
    payload = await _read_convert_payload(request)
    selection = payload.get("selection") if isinstance(payload.get("selection"), dict) else None
    if selection is None:
        article_ids = payload.get("article_ids")
        selection = {"mode": "ids", "article_ids": article_ids if isinstance(article_ids, list) else []}
    article_ids = resolve_article_ids_from_selection(selection)
    if not article_ids:
        raise HTTPException(status_code=400, detail="article_ids 不能为空")
    try:
        return submit_article_ingest(
            article_ids=article_ids,
            ai_enabled=bool(get_settings().ai_enabled),
            output_target=str(payload.get("output_target") or build_output_target(None)).strip(),
            skip_ingested=_parse_bool(payload.get("skip_ingested")) if payload.get("skip_ingested") is not None else True,
        )
    except Exception as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.post("/api/sync/login/qr/start")
async def post_sync_qr_start(
    request: Request,
    session_cookie: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
) -> dict[str, Any]:
    _require_access(session_cookie)
    _require_csrf(request, strict=True)
    try:
        return start_wechat_mp_qr_login()
    except Exception as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.get("/api/sync/login/qr/{session_id}")
async def get_sync_qr_status(
    session_id: str,
    session_cookie: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
) -> dict[str, Any]:
    _require_access(session_cookie)
    try:
        return get_wechat_mp_qr_login_status(session_id)
    except Exception as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.post("/api/sync/login/qr/{session_id}/confirm")
async def post_sync_qr_confirm(
    session_id: str,
    request: Request,
    session_cookie: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
) -> dict[str, Any]:
    _require_access(session_cookie)
    _require_csrf(request, strict=True)
    try:
        payload = confirm_wechat_mp_qr_login(session_id)
    except Exception as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    token = str(payload.get("token") or "").strip()
    cookie = str(payload.get("cookie") or "").strip()
    if token and cookie:
        save_wechat_mp_credentials(token, cookie)
    return payload


@router.get("/api/sync/ingest-jobs/{job_id}")
async def get_sync_ingest_job(
    job_id: str,
    session_cookie: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
) -> dict[str, Any]:
    _require_access(session_cookie)
    job = get_ingest_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="批量入库任务不存在")
    return job


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
        send_telegram_message(chat_id, "未识别到可用链接，请直接发送一条公众号或普通网页链接。")
        return {"status": "replied", "reason": "no_link"}
    if url_count > 1:
        send_telegram_message(chat_id, "一次只支持一条链接，请只发送一条公众号或普通网页链接。")
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
        _safe_send_feishu_message(open_id, "未识别到可用链接，请直接发送一条公众号或普通网页链接。")
        return {"status": "replied", "reason": "no_link"}
    if url_count > 1:
        _safe_send_feishu_message(open_id, "一次只支持一条链接，请只发送一条公众号或普通网页链接。")
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


def _require_access(session_cookie: str | None) -> dict[str, Any]:
    user = get_authenticated_user(session_cookie)
    if user is not None:
        return user
    raise HTTPException(status_code=401, detail="未授权访问")


def is_authenticated(session_cookie: str | None) -> bool:
    return get_authenticated_user(session_cookie) is not None


def get_authenticated_user(session_cookie: str | None) -> dict[str, Any] | None:
    ensure_admin_user_bootstrap()
    if not session_cookie:
        return None
    try:
        username, _ = str(session_cookie).split(":", 1)
    except ValueError:
        return None
    settings = get_settings()
    user = get_db_user(username)
    if user is None or str(user.get("status") or "active") != "active":
        return None
    if not verify_session_token(session_cookie, str(user.get("username") or ""), str(user.get("password_hash") or ""), settings.session_secret):
        return None
    return user


def _require_admin(session_cookie: str | None) -> dict[str, Any]:
    user = get_authenticated_user(session_cookie)
    if user is None:
        raise HTTPException(status_code=401, detail="未授权访问")
    if str(user.get("role") or "operator") != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return user


def _require_csrf(request: Request, *, strict: bool = False) -> None:
    header_token = str(request.headers.get("X-CSRF-Token") or "").strip()
    cookie_token = str(request.cookies.get(CSRF_COOKIE_NAME) or "").strip()
    if header_token and cookie_token and secrets.compare_digest(header_token, cookie_token):
        return
    if strict:
        raise HTTPException(status_code=403, detail="CSRF token 无效")
    looks_like_browser = bool(
        str(request.headers.get("origin") or "").strip()
        or str(request.headers.get("referer") or "").strip()
        or str(request.headers.get("sec-fetch-site") or "").strip()
    )
    if not looks_like_browser:
        return
    raise HTTPException(status_code=403, detail="CSRF token 无效")


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
