from __future__ import annotations

import multiprocessing as mp
import os
import queue
import re
import shutil
import threading
import time
import traceback
import uuid
import json
import hashlib
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from app.ai_adapters import extract_completion_preview, request_ai_completion, validate_provider_model
from app.ai_polish import apply_ai_polish_to_markdown
from app.content_sources import detect_source_type, extract_candidate_urls, fetch_article_from_url
from app.config import get_settings, update_feishu_webhook_state, update_telegram_webhook_state
from app.core.pipeline import run_article_pipeline, sanitize_filename
from app.source_cache import build_source_cache_key
from app.sync_db import SyncStore
from app.task_history import TaskHistoryStore
from app.wechat_sync import WechatMPClient, parse_sync_range


URL_PATTERN = re.compile(r"https?://mp\.weixin\.qq\.com/s(?:[/?][^\s)>]+)?", re.IGNORECASE)
TELEGRAM_SECRET_HEADER = "X-Telegram-Bot-Api-Secret-Token"
FEISHU_TENANT_TOKEN_URL = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
_task_history_store_lock = threading.Lock()
_task_history_store_cache: dict[str, TaskHistoryStore] = {}
_sync_store_lock = threading.Lock()
_sync_store_cache: dict[str, SyncStore] = {}
_mp_context = mp.get_context("spawn")


def run_pipeline(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return run_article_pipeline(*args, **kwargs)


def get_internal_workdir_root() -> Path:
    settings = get_settings()
    root = (settings.runtime_config_path.parent / "workdir").resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def create_internal_workdir(prefix: str) -> Path:
    workdir = get_internal_workdir_root() / f"{prefix}-{uuid.uuid4().hex[:12]}"
    workdir.mkdir(parents=True, exist_ok=False)
    return workdir


def cleanup_internal_workdir(path: Path | None) -> None:
    if path is None:
        return
    shutil.rmtree(path, ignore_errors=True)


def get_task_history_path() -> Path:
    settings = get_settings()
    return (settings.runtime_config_path.parent / "task-history.jsonl").resolve()


def get_task_history_store() -> TaskHistoryStore:
    path = get_task_history_path()
    cache_key = str(path)
    with _task_history_store_lock:
        store = _task_history_store_cache.get(cache_key)
        if store is None:
            store = TaskHistoryStore(path)
            _task_history_store_cache[cache_key] = store
        return store


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_sync_store_path() -> Path:
    settings = get_settings()
    return (settings.runtime_config_path.parent / "wechat-md-v5.sqlite3").resolve()


def get_sync_store() -> SyncStore:
    path = get_sync_store_path()
    cache_key = str(path)
    with _sync_store_lock:
        store = _sync_store_cache.get(cache_key)
        if store is None:
            store = SyncStore(path)
            store.initialize()
            _sync_store_cache[cache_key] = store
        return store


def _isolated_echo_worker(*, value: str) -> dict[str, Any]:
    return {"value": value}


def _isolated_sleep_worker(*, seconds: int) -> dict[str, Any]:
    time.sleep(int(seconds))
    return {"slept": int(seconds)}


def _isolated_single_conversion_worker(
    *,
    url: str,
    timeout: int,
    save_html: bool,
    output_target: str | None,
    ai_enabled: bool | None,
    require_ai_success: bool,
    batch_workspace_root: str | None,
    workspace_prefix: str,
    task_id: str | None,
    trigger_channel: str,
    rerun_of_task_id: str | None,
) -> dict[str, Any]:
    return _run_single_conversion(
        url=url,
        timeout=timeout,
        save_html=save_html,
        output_target=output_target,
        ai_enabled=ai_enabled,
        require_ai_success=require_ai_success,
        batch_workspace_root=Path(batch_workspace_root) if batch_workspace_root else None,
        workspace_prefix=workspace_prefix,
        task_id=task_id,
        trigger_channel=trigger_channel,
        rerun_of_task_id=rerun_of_task_id,
    )


_ISOLATED_WORKERS: dict[str, Any] = {
    "_isolated_echo_worker": _isolated_echo_worker,
    "_isolated_sleep_worker": _isolated_sleep_worker,
    "_isolated_single_conversion_worker": _isolated_single_conversion_worker,
}


def _isolated_worker_entry(worker_name: str, kwargs: dict[str, Any], result_queue) -> None:
    try:
        worker = _ISOLATED_WORKERS[worker_name]
    except KeyError as error:
        result_queue.put(
            {
                "ok": False,
                "error_type": "RuntimeError",
                "error": f"未知隔离 worker: {worker_name}",
                "traceback": "",
            }
        )
        raise RuntimeError(f"未知隔离 worker: {worker_name}") from error
    try:
        result_queue.put({"ok": True, "result": worker(**kwargs)})
    except Exception as error:  # pragma: no cover - exercised through parent wrapper
        result_queue.put(
            {
                "ok": False,
                "error_type": error.__class__.__name__,
                "error": str(error),
                "traceback": traceback.format_exc(),
            }
        )


def _invoke_isolated_worker(worker_name: str, kwargs: dict[str, Any], *, timeout_seconds: int) -> dict[str, Any]:
    result_queue = _mp_context.Queue()
    process = _mp_context.Process(
        target=_isolated_worker_entry,
        args=(worker_name, kwargs, result_queue),
    )
    process.start()
    process.join(timeout_seconds)

    if process.is_alive():
        process.terminate()
        process.join(5)
        if process.is_alive():  # pragma: no cover - defensive
            process.kill()
            process.join(1)
        raise TimeoutError(f"单篇转换硬超时（{timeout_seconds}s）")

    try:
        payload = result_queue.get(timeout=1)
    except queue.Empty as error:
        if process.exitcode and process.exitcode != 0:
            raise RuntimeError(f"隔离执行子进程异常退出（exit={process.exitcode}）") from error
        raise RuntimeError("隔离执行未返回结果") from error

    if payload.get("ok"):
        return dict(payload.get("result") or {})

    error_message = str(payload.get("error") or "隔离执行失败")
    error_type = str(payload.get("error_type") or "RuntimeError")
    if error_type == "TimeoutError":
        raise TimeoutError(error_message)
    raise RuntimeError(error_message)


def _run_single_conversion_isolated(
    *,
    url: str,
    timeout: int,
    save_html: bool,
    output_target: str | None,
    ai_enabled: bool | None,
    require_ai_success: bool,
    batch_workspace_root: Path | None,
    workspace_prefix: str,
    task_id: str | None,
    trigger_channel: str,
    rerun_of_task_id: str | None,
    hard_timeout_seconds: int,
) -> dict[str, Any]:
    return _invoke_isolated_worker(
        "_isolated_single_conversion_worker",
        {
            "url": url,
            "timeout": timeout,
            "save_html": save_html,
            "output_target": output_target,
            "ai_enabled": ai_enabled,
            "require_ai_success": require_ai_success,
            "batch_workspace_root": str(batch_workspace_root) if batch_workspace_root else None,
            "workspace_prefix": workspace_prefix,
            "task_id": task_id,
            "trigger_channel": trigger_channel,
            "rerun_of_task_id": rerun_of_task_id,
        },
        timeout_seconds=hard_timeout_seconds,
    )


def _prepare_conversion_tracking(
    *,
    url: str,
    trigger_channel: str,
    rerun_of_task_id: str | None,
    task_id: str | None,
) -> tuple[str, str]:
    source_type = detect_source_type(url)
    task_store = get_task_history_store()
    task_record = task_store.get_task(task_id) if task_id else None
    if task_record is None:
        task_record = task_store.create_task(
            trigger_channel=trigger_channel,
            source_type=source_type,
            source_url=url,
            rerun_of_task_id=rerun_of_task_id,
        )
        task_id = str(task_record["task_id"])
    else:
        task_id = str(task_record["task_id"])
    task_store.update_task(task_id, status="queued", source_type=source_type, source_url=url, error_message="")
    get_sync_store().upsert_article(
        {
            "article_url": url,
            "source_type": source_type,
            "fetch_status": "queued",
            "process_status": "queued",
            "last_task_id": task_id,
            "last_error": "",
            "cache_key": build_source_cache_key(url),
        }
    )
    return task_id, source_type


def _mark_conversion_dispatch_failure(
    *,
    url: str,
    task_id: str,
    source_type: str,
    error: Exception,
) -> None:
    message = str(error)
    get_task_history_store().update_task(
        task_id,
        status="error",
        source_type=source_type,
        error_message=message,
    )
    get_sync_store().update_article_status(
        url,
        fetch_status="error",
        process_status="error",
        last_task_id=task_id,
        last_error=message,
    )


class JobStore:
    def __init__(self) -> None:
        self._jobs: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._executor = ThreadPoolExecutor(max_workers=2)

    def create_batch_job(
        self,
        urls: list[str],
        output_dir: Path,
        save_html: bool,
        timeout: int,
        output_target: str,
        ai_enabled: bool | None = None,
        require_ai_success: bool = False,
        task_items: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        job_id = uuid.uuid4().hex
        payload = {
            "job_id": job_id,
            "status": "queued",
            "total": len(urls),
            "completed": 0,
            "success_count": 0,
            "failure_count": 0,
            "output_dir": str(output_dir),
            "save_html": save_html,
            "timeout": timeout,
            "output_target": output_target,
            "ai_enabled": bool(ai_enabled),
            "require_ai_success": bool(require_ai_success),
            "results": [],
            "errors": [],
        }
        with self._lock:
            self._jobs[job_id] = payload
        self._executor.submit(
            self._run_batch_job,
            job_id,
            task_items or [{"url": url, "task_id": ""} for url in urls],
            output_dir,
            save_html,
            timeout,
            output_target,
            ai_enabled,
            require_ai_success,
        )
        return payload.copy()

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return None if job is None else _copy_job(job)

    def _run_batch_job(
        self,
        job_id: str,
        task_items: list[dict[str, Any]],
        output_dir: Path,
        save_html: bool,
        timeout: int,
        output_target: str,
        ai_enabled: bool | None,
        require_ai_success: bool,
    ) -> None:
        ensure_runtime_environment()
        self._update(job_id, status="running")
        for item in task_items:
            url = str(item.get("url") or "").strip()
            task_id = str(item.get("task_id") or "").strip() or None
            try:
                conversion = execute_single_conversion(
                    url=url,
                    timeout=timeout,
                    save_html=save_html,
                    output_target=output_target,
                    ai_enabled=ai_enabled,
                    require_ai_success=require_ai_success,
                    trigger_channel="web",
                    task_id=task_id,
                    batch_workspace_root=output_dir if output_target != "fns" else None,
                    workspace_prefix=f"batch-{job_id[:8]}",
                )
                self._append_result(
                    job_id,
                    {
                        "url": url,
                        "task_id": conversion.get("task_id"),
                        "status": "success",
                        **conversion,
                    },
                )
            except Exception as error:  # pragma: no cover - exercised in integration flow
                self._append_result(
                    job_id,
                    {
                        "url": url,
                        "task_id": task_id,
                        "status": "error",
                        "error": str(error),
                    },
                )
        self._finalize(job_id)

    def _append_result(self, job_id: str, entry: dict[str, Any]) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job["results"].append(entry)
            job["completed"] += 1
            if entry["status"] == "success":
                job["success_count"] += 1
            else:
                job["failure_count"] += 1
                job["errors"].append({"url": entry["url"], "error": entry["error"]})

    def _finalize(self, job_id: str) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job["status"] = "completed"

    def _update(self, job_id: str, **fields: Any) -> None:
        with self._lock:
            self._jobs[job_id].update(fields)


def normalize_output_dir(output_dir: str | None) -> Path:
    settings = get_settings()
    return Path(output_dir).resolve() if output_dir else settings.default_output_dir


def execute_single_conversion(
    url: str,
    timeout: int | None = None,
    save_html: bool = False,
    output_target: str | None = None,
    ai_enabled: bool | None = None,
    require_ai_success: bool = False,
    trigger_channel: str = "web",
    rerun_of_task_id: str | None = None,
    task_id: str | None = None,
    *,
    batch_workspace_root: Path | None = None,
    workspace_prefix: str = "single",
) -> dict[str, Any]:
    settings = get_settings()
    normalized_timeout = int(timeout or settings.default_timeout)
    effective_task_id, source_type = _prepare_conversion_tracking(
        url=url,
        trigger_channel=trigger_channel,
        rerun_of_task_id=rerun_of_task_id,
        task_id=task_id,
    )
    try:
        if settings.single_conversion_isolation_enabled:
            return _run_single_conversion_isolated(
                url=url,
                timeout=normalized_timeout,
                save_html=save_html,
                output_target=output_target,
                ai_enabled=ai_enabled,
                require_ai_success=require_ai_success,
                batch_workspace_root=batch_workspace_root,
                workspace_prefix=workspace_prefix,
                task_id=effective_task_id,
                trigger_channel=trigger_channel,
                rerun_of_task_id=rerun_of_task_id,
                hard_timeout_seconds=settings.single_conversion_hard_timeout_seconds,
            )
        return _run_single_conversion(
            url=url,
            timeout=normalized_timeout,
            save_html=save_html,
            output_target=output_target,
            ai_enabled=ai_enabled,
            require_ai_success=require_ai_success,
            batch_workspace_root=batch_workspace_root,
            workspace_prefix=workspace_prefix,
            task_id=effective_task_id,
            trigger_channel=trigger_channel,
            rerun_of_task_id=rerun_of_task_id,
        )
    except Exception as error:
        _mark_conversion_dispatch_failure(
            url=url,
            task_id=effective_task_id,
            source_type=source_type,
            error=error,
        )
        raise


def _run_single_conversion(
    *,
    url: str,
    timeout: int,
    save_html: bool,
    output_target: str | None,
    ai_enabled: bool | None,
    require_ai_success: bool = False,
    batch_workspace_root: Path | None = None,
    workspace_prefix: str = "single",
    task_id: str | None = None,
    trigger_channel: str = "web",
    rerun_of_task_id: str | None = None,
) -> dict[str, Any]:
    settings = get_settings()
    sync_store = get_sync_store()
    normalized_target = build_output_target(output_target, settings)
    normalized_timeout = int(timeout or settings.default_timeout)
    normalized_ai_enabled = resolve_ai_enabled(ai_enabled, settings)
    ensure_runtime_environment()
    source_type = detect_source_type(url)
    task_store = get_task_history_store()
    task_record = task_store.get_task(task_id) if task_id else None
    if task_record is None:
        task_record = task_store.create_task(
            trigger_channel=trigger_channel,
            source_type=source_type,
            source_url=url,
            rerun_of_task_id=rerun_of_task_id,
        )
        task_id = str(task_record["task_id"])
    task_store.update_task(task_id, status="running", source_type=source_type, source_url=url)
    fetch_meta: dict[str, Any] = {
        "fetch_status": "queued",
        "content_kind": "unknown",
        "comment_id": "",
        "failure_reason": "",
        "cache_hit": False,
    }
    artifacts: list[dict[str, Any]] = []
    sync_store.upsert_article(
        {
            "article_url": url,
            "source_type": source_type,
            "fetch_status": "queued",
            "process_status": "running",
            "last_task_id": task_id,
            "cache_key": build_source_cache_key(url),
        }
    )

    workspace: Path | None = None
    output_dir = batch_workspace_root or normalize_output_dir(None)
    if normalized_target == "fns":
        workspace = create_internal_workdir(workspace_prefix)
        output_dir = workspace

    try:
        resolved_source_type, article, source_html, fetch_meta = fetch_article_from_url(
            url,
            timeout=normalized_timeout,
        )
        result = run_pipeline(
            article=article,
            output_base_dir=output_dir,
            save_html=save_html,
            timeout=normalized_timeout,
            source_html=source_html,
        )
        source_type = resolved_source_type
        result["fetch_status"] = str(fetch_meta.get("fetch_status") or "success")
        result["content_kind"] = str(fetch_meta.get("content_kind") or "unknown")
        result["comment_id"] = str(fetch_meta.get("comment_id") or "")
        result["cache_hit"] = bool(fetch_meta.get("cache_hit"))
        result["failure_reason"] = str(fetch_meta.get("failure_reason") or "")
        result.setdefault("image_mode", settings.image_mode)
        ai_polish = {
            "enabled": normalized_ai_enabled,
            "status": "skipped",
            "model": settings.ai_model if normalized_ai_enabled else None,
            "template_applied": False,
            "message": "AI 润色未启用",
        }
        if normalized_ai_enabled:
            try:
                ai_polish = apply_ai_polish_to_result(
                    result=result,
                    url=url,
                    timeout=normalized_timeout,
                )
            except Exception as error:
                ai_polish = {
                    "enabled": True,
                    "status": "failed",
                    "model": settings.ai_model,
                    "template_applied": False,
                    "message": str(error),
                }
                if require_ai_success:
                    raise RuntimeError(f"AI 润色失败：{error}") from error
            if require_ai_success and not bool(ai_polish.get("template_applied")):
                message = str(ai_polish.get("message") or "模板未成功应用")
                raise RuntimeError(f"AI 润色失败：{message}")
        sync = sync_result_to_output(result, output_target=normalized_target)
        artifacts = _record_conversion_artifacts(
            url=url,
            result=result,
            fetch_meta=fetch_meta,
            sync=sync,
        )
        ingested_at = _utc_now() if normalized_target == "fns" else ""
        markdown_path = Path(str(result.get("markdown_file") or ""))
        content_hash = (
            hashlib.sha256(markdown_path.read_bytes()).hexdigest()
            if markdown_path.exists()
            else ""
        )
        sync_store.upsert_article(
            {
                "article_url": url,
                "source_type": source_type,
                "account_name": str(result.get("account_name") or ""),
                "title": str(result.get("title") or ""),
                "author": str(result.get("author") or ""),
                "content_kind": str(fetch_meta.get("content_kind") or "unknown"),
                "fetch_status": str(fetch_meta.get("fetch_status") or "success"),
                "process_status": "success",
                "is_ingested": normalized_target == "fns",
                "cleaned_at": _utc_now(),
                "ingested_at": ingested_at,
                "last_task_id": task_id,
                "last_error": "",
                "comment_id": str(fetch_meta.get("comment_id") or ""),
                "cache_key": str(fetch_meta.get("cache_key") or build_source_cache_key(url)),
                "cache_hit_count": 1 if fetch_meta.get("cache_hit") else 0,
                "raw_html_path": str(fetch_meta.get("source_html_path") or ""),
                "normalized_json_path": str(fetch_meta.get("normalized_path") or ""),
                "latest_markdown_path": str(result.get("markdown_file") or ""),
                "content_hash": content_hash,
                "publish_time": int(result.get("publish_time") or 0),
            }
        )
        local_artifacts = {"retained": False, "workdir": None}
        if workspace is not None:
            if settings.cleanup_temp_on_success:
                cleanup_internal_workdir(workspace)
            else:
                local_artifacts = {"retained": True, "workdir": str(workspace)}
        task_store.update_task(
            task_id,
            status="success",
            source_type=source_type,
            note_title=str(result.get("title") or ""),
            sync_path=str(sync.get("path") or sync.get("markdown_file") or ""),
            error_message="",
        )
    except Exception as error:
        sync_store.update_article_status(
            url,
            fetch_status=str(fetch_meta.get("fetch_status") or "error"),
            process_status="error",
            last_task_id=task_id,
            last_error=str(error),
        )
        if task_id:
            task_store.update_task(
                task_id,
                status="error",
                source_type=source_type,
                error_message=str(error),
            )
        cleanup_internal_workdir(workspace)
        raise

    return {
        "status": "success",
        "task_id": task_id,
        "source_type": source_type,
        "output_target": normalized_target,
        "result": result,
        "sync": sync,
        "local_artifacts": local_artifacts,
        "ai_polish": ai_polish,
        "fetch_status": str(fetch_meta.get("fetch_status") or "success"),
        "content_kind": str(fetch_meta.get("content_kind") or "unknown"),
        "cache_hit": bool(fetch_meta.get("cache_hit")),
        "failure_reason": str(fetch_meta.get("failure_reason") or ""),
        "artifacts": artifacts,
    }


def _record_conversion_artifacts(
    *,
    url: str,
    result: dict[str, Any],
    fetch_meta: dict[str, Any],
    sync: dict[str, Any],
) -> list[dict[str, Any]]:
    store = get_sync_store()
    recorded: list[dict[str, Any]] = []
    html_path = str(fetch_meta.get("source_html_path") or "").strip()
    normalized_path = str(fetch_meta.get("normalized_path") or "").strip()
    markdown_path = str(result.get("markdown_file") or "").strip()
    rendered_html_path = str(result.get("html_file") or "").strip()
    sync_path = str(sync.get("path") or "").strip()
    if html_path:
        recorded.append(store.record_artifact(url, "source_html", html_path))
    if normalized_path:
        recorded.append(store.record_artifact(url, "normalized_json", normalized_path))
    if markdown_path:
        recorded.append(store.record_artifact(url, "markdown", markdown_path))
    if rendered_html_path:
        recorded.append(store.record_artifact(url, "rendered_html", rendered_html_path))
    if sync_path:
        recorded.append(store.record_artifact(url, "fns_note", sync_path))
    return recorded


def ensure_runtime_environment() -> None:
    settings = get_settings()
    os.environ["WECHAT_MD_IMAGE_MODE"] = settings.image_mode
    os.environ["WECHAT_MD_IMAGE_STORAGE_PROVIDER"] = settings.image_storage_provider or "s3"
    os.environ["WECHAT_MD_IMAGE_STORAGE_ENDPOINT"] = settings.image_storage_endpoint or ""
    os.environ["WECHAT_MD_IMAGE_STORAGE_REGION"] = settings.image_storage_region or ""
    os.environ["WECHAT_MD_IMAGE_STORAGE_BUCKET"] = settings.image_storage_bucket or ""
    os.environ["WECHAT_MD_IMAGE_STORAGE_ACCESS_KEY_ID"] = settings.image_storage_access_key_id or ""
    os.environ["WECHAT_MD_IMAGE_STORAGE_SECRET_ACCESS_KEY"] = settings.image_storage_secret_access_key or ""
    os.environ["WECHAT_MD_IMAGE_STORAGE_PATH_TEMPLATE"] = settings.image_storage_path_template or ""
    os.environ["WECHAT_MD_IMAGE_STORAGE_PUBLIC_BASE_URL"] = settings.image_storage_public_base_url or ""


def parse_links(urls: list[str] | None = None, urls_text: str | None = None, file_text: str | None = None) -> list[str]:
    raw_values: list[str] = []
    for source in urls or []:
        if source:
            raw_values.append(source.strip())
    for blob in (urls_text or "", file_text or ""):
        raw_values.extend(extract_candidate_urls(blob))

    deduped: list[str] = []
    seen: set[str] = set()
    for item in raw_values:
        try:
            detect_source_type(item)
        except ValueError:
            continue
        if item not in seen:
            deduped.append(item)
            seen.add(item)
    return deduped


def build_sync_config_payload() -> dict[str, Any]:
    settings = get_settings()
    return {
        "wechat_mp_configured": settings.wechat_mp_configured,
        "wechat_mp_token_configured": bool(settings.wechat_mp_token),
        "wechat_mp_token_masked": "*" * 8 if settings.wechat_mp_token else "",
        "wechat_mp_cookie_configured": bool(settings.wechat_mp_cookie),
        "wechat_mp_cookie_masked": _mask_cookie(settings.wechat_mp_cookie),
    }


def check_wechat_mp_login_status(http_session=None) -> dict[str, Any]:
    settings = get_settings()
    if not settings.wechat_mp_configured:
        return {
            "configured": False,
            "valid": False,
            "message": "公众号后台 token / cookie 未配置",
        }
    client = WechatMPClient(http_session=http_session)
    return client.check_login_status()


def search_wechat_accounts(keyword: str, *, begin: int = 0, size: int = 5, http_session=None) -> dict[str, Any]:
    client = WechatMPClient(http_session=http_session)
    return client.search_accounts(keyword=keyword, begin=begin, size=size)


def list_sync_sources_payload() -> dict[str, Any]:
    return {"items": get_sync_store().list_sync_sources()}


def create_sync_source(payload: dict[str, Any]) -> dict[str, Any]:
    fakeid = str(payload.get("fakeid") or "").strip()
    if not fakeid:
        raise ValueError("fakeid 不能为空")
    store = get_sync_store()
    account = store.upsert_account(
        {
            "fakeid": fakeid,
            "nickname": str(payload.get("nickname") or "").strip(),
            "alias": str(payload.get("alias") or "").strip(),
            "round_head_img": str(payload.get("round_head_img") or "").strip(),
            "service_type": int(payload.get("service_type") or 0),
            "signature": str(payload.get("signature") or "").strip(),
        }
    )
    source = store.create_or_update_sync_source(fakeid)
    return {"account": account, "source": source}


def delete_sync_source(source_id: str) -> None:
    get_sync_store().delete_sync_source(source_id)


def sync_source_articles(
    *,
    source_id: str,
    start_date: str | None = None,
    end_date: str | None = None,
    queue_for_ingest: bool = False,
    ai_enabled: bool = False,
    output_target: str = "fns",
    skip_ingested: bool = True,
    http_session=None,
) -> dict[str, Any]:
    store = get_sync_store()
    source = store.get_sync_source(source_id)
    if source is None:
        raise ValueError("同步源不存在")

    if start_date and end_date:
        sync_range = parse_sync_range(start_date, end_date)
        mode = "manual"
    else:
        latest_update_time = int(source.get("latest_article_update_time") or 0)
        if not latest_update_time:
            raise ValueError("首次同步必须显式提供开始和结束日期")
        start_dt = datetime.fromtimestamp(latest_update_time + 1, tz=timezone.utc).date().isoformat()
        end_dt = datetime.now(timezone.utc).date().isoformat()
        sync_range = parse_sync_range(start_dt, end_dt)
        mode = "incremental"

    run = store.create_sync_run(
        source["id"],
        mode=mode,
        range_start=sync_range.start_date,
        range_end=sync_range.end_date,
    )
    client = WechatMPClient(http_session=http_session)
    fetched_count = 0
    new_count = 0
    updated_count = 0
    article_ids: list[str] = []
    begin = 0
    latest_publish = int(source.get("latest_article_update_time") or 0)
    try:
        while True:
            payload = client.fetch_articles(source["account_fakeid"], begin=begin, size=10)
            items = payload.get("items") if isinstance(payload.get("items"), list) else []
            if not items:
                break
            fetched_count += len(items)
            stop_after_page = False
            for item in items:
                publish_time = int(item.get("publish_time") or item.get("create_time") or 0)
                if publish_time and publish_time < sync_range.start_ts:
                    stop_after_page = True
                    continue
                if publish_time and publish_time > sync_range.end_ts:
                    continue
                article, is_new = store.upsert_article(
                    {
                        "article_url": str(item.get("article_url") or "").strip(),
                        "source_type": "wechat",
                        "account_fakeid": str(source.get("account_fakeid") or "").strip(),
                        "account_name": str(source.get("account_name") or "").strip(),
                        "title": str(item.get("title") or "").strip(),
                        "author": str(item.get("author") or "").strip(),
                        "digest": str(item.get("digest") or "").strip(),
                        "cover": str(item.get("cover") or "").strip(),
                        "publish_time": publish_time,
                        "create_time": int(item.get("create_time") or 0),
                        "content_kind": str(item.get("content_kind") or "article"),
                        "fetch_status": "indexed",
                        "process_status": "pending",
                    }
                )
                if is_new:
                    new_count += 1
                else:
                    updated_count += 1
                latest_publish = max(latest_publish, publish_time)
                article_ids.append(str(article.get("id") or ""))
            if stop_after_page:
                break
            begin += len(items)
        now = _utc_now()
        store.update_sync_source_state(
            source["id"],
            last_sync_at=now,
            last_range_start=sync_range.start_date,
            last_range_end=sync_range.end_date,
            latest_article_update_time=latest_publish,
        )
        queued_count = 0
        ingest_job = None
        if queue_for_ingest and article_ids:
            ingest_job = submit_article_ingest(
                article_ids=article_ids,
                ai_enabled=ai_enabled,
                output_target=output_target,
                skip_ingested=skip_ingested,
            )
            queued_count = int(ingest_job.get("total") or 0)
        store.finish_sync_run(
            run["id"],
            status="completed",
            fetched_count=fetched_count,
            new_count=new_count,
            updated_count=updated_count,
            queued_count=queued_count,
        )
        return {
            "run_id": run["id"],
            "status": "completed",
            "fetched_count": fetched_count,
            "new_count": new_count,
            "updated_count": updated_count,
            "queued_count": queued_count,
            "ingest_job": ingest_job,
        }
    except Exception as error:
        store.finish_sync_run(
            run["id"],
            status="error",
            fetched_count=fetched_count,
            new_count=new_count,
            updated_count=updated_count,
            queued_count=0,
            error_message=str(error),
        )
        raise


def list_sync_articles(
    *,
    account_fakeid: str | None = None,
    process_status: str | None = None,
    is_ingested: bool | None = None,
    published_from: int | None = None,
    published_to: int | None = None,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    return get_sync_store().list_articles(
        account_fakeid=account_fakeid,
        process_status=process_status,
        is_ingested=is_ingested,
        published_from=published_from,
        published_to=published_to,
        limit=limit,
        offset=offset,
    )


def submit_article_ingest(
    *,
    article_ids: list[str],
    ai_enabled: bool,
    output_target: str,
    skip_ingested: bool,
) -> dict[str, Any]:
    store = get_sync_store()
    filtered_ids = [str(item).strip() for item in article_ids if str(item).strip()]
    if not filtered_ids:
        raise ValueError("article_ids 不能为空")
    job = store.create_ingest_job(
        total=len(filtered_ids),
        ai_enabled=ai_enabled,
        output_target=output_target,
        skip_ingested=skip_ingested,
    )
    _ingest_executor.submit(
        _run_ingest_job,
        job["id"],
        filtered_ids,
        ai_enabled,
        output_target,
        skip_ingested,
    )
    return job


def get_ingest_job(job_id: str) -> dict[str, Any] | None:
    return get_sync_store().get_ingest_job(job_id)


def _run_ingest_job(job_id: str, article_ids: list[str], ai_enabled: bool, output_target: str, skip_ingested: bool) -> None:
    store = get_sync_store()
    store.update_ingest_job(job_id, status="running")
    completed = 0
    success_count = 0
    failure_count = 0
    last_error = ""
    for article_id in article_ids:
        article = store.get_article_by_id(article_id)
        if article is None:
            completed += 1
            failure_count += 1
            last_error = "文章不存在"
            store.update_ingest_job(
                job_id,
                completed=completed,
                success_count=success_count,
                failure_count=failure_count,
                error_message=last_error,
            )
            continue
        if skip_ingested and bool(article.get("is_ingested")):
            completed += 1
            success_count += 1
            store.update_article_status(
                str(article.get("article_url") or ""),
                process_status="success",
                last_error="",
            )
            store.update_ingest_job(
                job_id,
                completed=completed,
                success_count=success_count,
                failure_count=failure_count,
            )
            continue
        store.update_article_status(str(article.get("article_url") or ""), process_status="running", last_error="")
        try:
            execute_single_conversion(
                url=str(article.get("article_url") or ""),
                timeout=get_settings().default_timeout,
                save_html=False,
                output_target=output_target,
                ai_enabled=ai_enabled,
                require_ai_success=bool(ai_enabled),
                trigger_channel="web",
                task_id=None,
            )
            completed += 1
            success_count += 1
        except Exception as error:
            completed += 1
            failure_count += 1
            last_error = str(error)
        store.update_ingest_job(
            job_id,
            completed=completed,
            success_count=success_count,
            failure_count=failure_count,
            error_message=last_error if failure_count else "",
        )
    store.update_ingest_job(
        job_id,
        status="completed" if failure_count == 0 else "error",
        completed=completed,
        success_count=success_count,
        failure_count=failure_count,
        error_message=last_error if failure_count else "",
    )


def _mask_cookie(value: str | None) -> str:
    if not value:
        return ""
    text = str(value)
    if len(text) <= 12:
        return "*" * len(text)
    return f"{text[:6]}...{text[-6:]}"


def list_tasks(
    *,
    trigger_channel: str | None = None,
    source_type: str | None = None,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    return get_task_history_store().list_tasks(
        trigger_channel=trigger_channel,
        source_type=source_type,
        status=status,
        limit=limit,
        offset=offset,
    )


def get_task(task_id: str) -> dict[str, Any] | None:
    return get_task_history_store().get_task(task_id)


def submit_rerun_task(task_id: str) -> dict[str, Any]:
    original = get_task_history_store().get_task(task_id)
    if original is None:
        raise KeyError(f"任务不存在: {task_id}")
    url = str(original.get("source_url") or "").strip()
    trigger_channel = str(original.get("trigger_channel") or "web").strip() or "web"
    source_type = detect_source_type(url)
    rerun_task = get_task_history_store().create_task(
        trigger_channel=trigger_channel,
        source_type=source_type,
        source_url=url,
        rerun_of_task_id=task_id,
    )
    _rerun_executor.submit(
        execute_single_conversion,
        url=url,
        timeout=get_settings().default_timeout,
        save_html=False,
        output_target="fns" if get_settings().fns_enabled else "local",
        ai_enabled=None,
        require_ai_success=True,
        trigger_channel=trigger_channel,
        rerun_of_task_id=task_id,
        task_id=str(rerun_task["task_id"]),
        workspace_prefix="rerun",
    )
    return {"task_id": str(rerun_task["task_id"]), "rerun_of_task_id": task_id}


def submit_rerun_tasks(task_ids: list[str]) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    for task_id in task_ids:
        items.append(submit_rerun_task(str(task_id)))
    return {"accepted": len(items), "items": items}


def read_uploaded_text(content: bytes) -> str:
    for encoding in ("utf-8", "utf-8-sig", "gbk"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    return content.decode("utf-8", errors="ignore")


def build_config_payload() -> dict[str, Any]:
    settings = get_settings()
    default_output_target = build_output_target(None, settings)
    return {
        "default_output_dir": str(settings.default_output_dir),
        "service_mode": "hybrid" if settings.fns_enabled else "local_only",
        "default_output_target": default_output_target,
        "auth_enabled": True,
        "session_cookie_secure": settings.session_cookie_secure,
        "fns_enabled": settings.fns_enabled,
        "fns_base_url": settings.fns_base_url,
        "fns_vault": settings.fns_vault,
        "fns_target_dir": settings.fns_target_dir,
        "current_user": {"username": settings.username},
        "cleanup_temp_on_success": settings.cleanup_temp_on_success,
        "image_mode": settings.image_mode,
        "image_storage_enabled": settings.image_storage_enabled,
        "image_public_base_url": settings.image_storage_public_base_url,
        "image_storage_bucket": settings.image_storage_bucket,
        "image_storage_path_template": settings.image_storage_path_template,
        "feishu_enabled": settings.feishu_enabled,
        "feishu_webhook_status": settings.feishu_webhook_status,
        "ai_enabled": settings.ai_enabled,
        "ai_configured": settings.ai_configured,
        "ai_model": settings.ai_model,
        "ai_selected_provider": (settings.ai_selected_provider or {}).get("type"),
        "ai_enable_content_polish": settings.ai_enable_content_polish,
        "ai_template_source": settings.ai_template_source,
    }


def resolve_ai_enabled(ai_enabled: bool | None, settings=None) -> bool:
    settings = settings or get_settings()
    if ai_enabled is None:
        return settings.ai_enabled
    return bool(ai_enabled)


def apply_ai_polish_to_result(
    *,
    result: dict[str, Any],
    url: str,
    timeout: int,
) -> dict[str, Any]:
    settings = get_settings()
    if not settings.ai_configured:
        raise RuntimeError("AI 润色尚未配置完整")
    markdown_path = Path(str(result["markdown_file"]))
    ai_result = apply_ai_polish_to_markdown(
        markdown_path=markdown_path,
        metadata={
            "title": str(result.get("title") or ""),
            "author": str(result.get("author") or ""),
            "url": str(result.get("original_url") or url),
        },
        provider=dict(settings.ai_selected_provider or {}),
        model=dict(settings.ai_selected_model or {}),
        interpreter_prompt=settings.ai_prompt_template,
        frontmatter_template=settings.ai_frontmatter_template,
        body_template=settings.ai_body_template,
        context_template=settings.ai_context_template,
        allow_body_polish=settings.ai_allow_body_polish,
        enable_content_polish=settings.ai_enable_content_polish,
        content_polish_prompt=settings.ai_content_polish_prompt,
        timeout=max(timeout, 60),
    )
    result["ai_polish"] = ai_result
    return ai_result


def test_ai_connectivity(
    *,
    provider: dict[str, Any],
    model: dict[str, Any],
    timeout: int = 30,
    http_session=None,
) -> dict[str, Any]:
    normalized_provider = dict(provider or {})
    normalized_model = dict(model or {})
    validate_provider_model(normalized_provider, normalized_model)
    started = time.perf_counter()
    try:
        payload = request_ai_completion(
            provider=normalized_provider,
            model=normalized_model,
            messages=[
                {"role": "system", "content": "你是连通性测试助手，只返回极简文本。"},
                {"role": "user", "content": "请返回 JSON：{\"pong\":\"ok\"}"},
            ],
            timeout=timeout,
            http_session=http_session,
            temperature=0,
            max_tokens=32,
        )
    except requests.Timeout as error:
        return {
            "success": False,
            "latency_ms": int((time.perf_counter() - started) * 1000),
            "model": str(normalized_model.get("model_id") or ""),
            "preview": "",
            "message": f"请求超时: {error}",
        }
    except requests.RequestException as error:
        return {
            "success": False,
            "latency_ms": int((time.perf_counter() - started) * 1000),
            "model": str(normalized_model.get("model_id") or ""),
            "preview": "",
            "message": f"请求失败: {error}",
        }
    except ValueError as error:
        return {
            "success": False,
            "latency_ms": int((time.perf_counter() - started) * 1000),
            "model": str(normalized_model.get("model_id") or ""),
            "preview": "",
            "message": f"响应不是有效 JSON: {error}",
        }
    except RuntimeError as error:
        return {
            "success": False,
            "latency_ms": int((time.perf_counter() - started) * 1000),
            "model": str(normalized_model.get("model_id") or ""),
            "preview": "",
            "message": str(error),
        }

    preview = extract_completion_preview(payload, provider_type=str(normalized_provider.get("type") or "openai_compatible"))
    return {
        "success": True,
        "latency_ms": int((time.perf_counter() - started) * 1000),
        "model": str(payload.get("model") or normalized_model.get("model_id") or ""),
        "preview": preview,
        "message": "连接正常",
    }


def check_fns_status(http_session=None) -> dict[str, Any]:
    settings = get_settings()
    payload: dict[str, Any] = {
        "configured": settings.fns_enabled,
        "connected": False,
        "vault_exists": False,
        "vault_name": settings.fns_vault,
        "vault_count": 0,
        "user": None,
        "base_url": settings.fns_base_url,
        "message": "",
    }
    if not settings.fns_enabled:
        payload["message"] = "FNS 尚未配置完整"
        return payload

    session = http_session or requests.Session()
    headers = {"token": str(settings.fns_token)}
    try:
        user_response = session.get(
            f"{settings.fns_base_url}/api/user/info",
            headers=headers,
            timeout=max(settings.default_timeout, 15),
        )
        user_response.raise_for_status()
        vault_response = session.get(
            f"{settings.fns_base_url}/api/vault",
            headers=headers,
            timeout=max(settings.default_timeout, 15),
        )
        vault_response.raise_for_status()
        user_data = user_response.json()
        vault_data = vault_response.json()
    except requests.RequestException as error:
        payload["message"] = f"连接失败: {error}"
        return payload
    except ValueError:
        payload["message"] = "FNS 返回了无法解析的 JSON"
        return payload

    user_block = user_data.get("data") if isinstance(user_data, dict) else None
    vault_list = vault_data.get("data") if isinstance(vault_data, dict) else []
    if not isinstance(vault_list, list):
        vault_list = []
    payload["connected"] = True
    payload["user"] = user_block if isinstance(user_block, dict) else None
    payload["vault_count"] = len(vault_list)
    payload["vault_exists"] = any(
        isinstance(item, dict) and str(item.get("vault") or "") == str(settings.fns_vault or "")
        for item in vault_list
    )
    payload["message"] = "连接正常" if payload["vault_exists"] else "连接正常，但目标 vault 不存在"
    return payload


def build_output_target(output_target: str | None, settings=None) -> str:
    settings = settings or get_settings()
    normalized = (output_target or "").strip().lower()
    if normalized:
        if normalized not in {"local", "fns"}:
            raise ValueError("output_target 仅支持 local 或 fns")
        if normalized == "fns" and not settings.fns_enabled:
            raise ValueError("Fast Note Sync 未完成配置，无法输出到 fns")
        return normalized
    return "fns" if settings.fns_enabled else "local"


def sync_result_to_output(result: dict[str, Any], output_target: str) -> dict[str, Any]:
    if output_target == "local":
        return {
            "status": "success",
            "target": "local",
            "markdown_file": result["markdown_file"],
        }
    return sync_markdown_to_fns(
        markdown_path=Path(str(result["markdown_file"])),
        note_title=str(result["title"]),
        folder_name=str(result.get("folder_name") or ""),
    )


def sync_markdown_to_fns(
    markdown_path: Path,
    note_title: str,
    folder_name: str,
    http_session=None,
) -> dict[str, Any]:
    settings = get_settings()
    if not settings.fns_enabled:
        raise RuntimeError("Fast Note Sync 未完成配置")

    file_name = f"{sanitize_filename(note_title)}.md"
    target_dir = settings.fns_target_dir.strip("/\\")
    note_path = "/".join(part for part in (target_dir, file_name) if part)
    content = markdown_path.read_text(encoding="utf-8")
    stat = markdown_path.stat()
    payload = {
        "vault": settings.fns_vault,
        "path": note_path,
        "content": content,
        "createOnly": False,
        "ctime": int(stat.st_ctime),
        "mtime": int(stat.st_mtime),
    }
    session = http_session or requests.Session()
    try:
        response = session.post(
            f"{settings.fns_base_url}/api/note",
            headers={
                "token": str(settings.fns_token),
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=max(get_settings().default_timeout, 30),
        )
        response.raise_for_status()
    except requests.RequestException as error:
        raise RuntimeError(f"Fast Note Sync 请求失败: {error}") from error

    try:
        data = response.json()
    except ValueError as error:
        raise RuntimeError("Fast Note Sync 返回了无法解析的 JSON") from error

    if isinstance(data, dict):
        success_flag = data.get("status")
        code = data.get("code")
        if success_flag is False or code not in (None, 0, 1):
            raise RuntimeError(
                f"Fast Note Sync 写入失败: {data.get('msg') or data.get('message') or data.get('code')}"
            )

    return {
        "status": "success",
        "target": "fns",
        "vault": settings.fns_vault,
        "path": note_path,
        "folder_name": folder_name,
        "response": data,
    }


def configure_telegram_webhook(http_session=None) -> dict[str, Any]:
    settings = get_settings()
    if not settings.telegram_bot_token:
        state = {"status": "inactive", "message": "Telegram Bot Token 未配置", "webhook_url": ""}
        update_telegram_webhook_state(state["status"], state["message"])
        return state

    session = http_session or requests.Session()
    if not settings.telegram_enabled:
        response = session.post(
            _telegram_api_url(settings.telegram_bot_token, "deleteWebhook"),
            json={"drop_pending_updates": False},
            timeout=max(settings.default_timeout, 15),
        )
        response.raise_for_status()
        state = {"status": "inactive", "message": "Telegram Webhook 已删除", "webhook_url": ""}
        update_telegram_webhook_state(state["status"], state["message"])
        return state

    if not settings.telegram_enabled_and_configured or not settings.telegram_webhook_url:
        state = {"status": "error", "message": "Telegram Bot 配置不完整", "webhook_url": settings.telegram_webhook_url or ""}
        update_telegram_webhook_state(state["status"], state["message"])
        return state

    response = session.post(
        _telegram_api_url(settings.telegram_bot_token, "setWebhook"),
        json={
            "url": settings.telegram_webhook_url,
            "secret_token": settings.telegram_webhook_secret,
            "allowed_updates": ["message"],
        },
        timeout=max(settings.default_timeout, 15),
    )
    response.raise_for_status()
    payload = response.json()
    ok = bool(payload.get("ok", False))
    description = str(payload.get("description") or ("Telegram Webhook 已注册" if ok else "Telegram Webhook 注册失败"))
    state = {
        "status": "success" if ok else "error",
        "message": description,
        "webhook_url": settings.telegram_webhook_url,
    }
    update_telegram_webhook_state(state["status"], state["message"])
    return state


def send_telegram_message(chat_id: str, text: str, http_session=None) -> dict[str, Any]:
    settings = get_settings()
    if not settings.telegram_bot_token:
        raise RuntimeError("Telegram Bot Token 未配置")
    session = http_session or requests.Session()
    response = session.post(
        _telegram_api_url(settings.telegram_bot_token, "sendMessage"),
        json={"chat_id": chat_id, "text": text},
        timeout=max(settings.default_timeout, 15),
    )
    response.raise_for_status()
    return response.json()


def submit_telegram_convert_task(url: str, chat_id: str) -> None:
    task = get_task_history_store().create_task(
        trigger_channel="telegram",
        source_type=detect_source_type(url),
        source_url=url,
    )
    _telegram_executor.submit(process_telegram_convert_task, url, chat_id, str(task["task_id"]))


def process_telegram_convert_task(url: str, chat_id: str, task_id: str | None = None) -> None:
    settings = get_settings()
    try:
        payload = execute_single_conversion(
            url=url,
            timeout=settings.default_timeout,
            save_html=False,
            output_target="fns",
            require_ai_success=True,
            trigger_channel="telegram",
            rerun_of_task_id=None,
            task_id=task_id,
        )
    except Exception as error:
        print(f"[telegram] conversion failed chat_id={chat_id}: {error}")
        send_telegram_message(chat_id, f"转换失败：{error}")
        return

    ai_polish = payload.get("ai_polish") if isinstance(payload, dict) else {}
    if isinstance(ai_polish, dict):
        print(
            "[telegram] ai result "
            f"chat_id={chat_id} enabled={bool(ai_polish.get('enabled'))} "
            f"status={ai_polish.get('status')} "
            f"template_applied={bool(ai_polish.get('template_applied'))} "
            f"content_polished={bool(ai_polish.get('content_polished'))}"
        )
    print(
        "[telegram] conversion synced "
        f"chat_id={chat_id} path={payload['sync'].get('path') or payload['sync'].get('markdown_file') or '-'}"
    )

    if not settings.telegram_notify_on_complete:
        return

    title = str(payload["result"].get("title") or "转换完成")
    sync_path = str(payload["sync"].get("path") or payload["sync"].get("markdown_file") or "-")
    resolved_image_mode = str(
        payload["result"].get("image_mode")
        or payload.get("image_mode")
        or settings.image_mode
        or ""
    )
    image_mode = "S3 图床外链" if resolved_image_mode == "s3_hotlink" else "微信原链"
    send_telegram_message(
        chat_id,
        "\n".join(
            [
                f"转换完成：{title}",
                f"同步路径：{sync_path}",
                f"图片模式：{image_mode}",
            ]
        ),
    )


def send_feishu_message(open_id: str, text: str, http_session=None) -> dict[str, Any]:
    settings = get_settings()
    if not settings.feishu_app_id or not settings.feishu_app_secret:
        raise RuntimeError("飞书 App ID / App Secret 未配置")
    session = http_session or requests.Session()
    tenant_access_token = get_feishu_tenant_access_token(http_session=session)
    response = session.post(
        "https://open.feishu.cn/open-apis/im/v1/messages",
        params={"receive_id_type": "open_id"},
        headers={
            "Authorization": f"Bearer {tenant_access_token}",
            "Content-Type": "application/json; charset=utf-8",
        },
        json={
            "receive_id": open_id,
            "msg_type": "text",
            "content": json.dumps({"text": text}, ensure_ascii=False),
        },
        timeout=max(settings.default_timeout, 15),
    )
    try:
        response.raise_for_status()
    except requests.HTTPError as error:
        detail = response.text.strip()
        if detail:
            raise RuntimeError(f"飞书发送消息失败: {response.status_code} {detail}") from error
        raise RuntimeError(f"飞书发送消息失败: HTTP {response.status_code}") from error
    return response.json()


def get_feishu_tenant_access_token(http_session=None) -> str:
    settings = get_settings()
    if not settings.feishu_app_id or not settings.feishu_app_secret:
        raise RuntimeError("飞书 App ID / App Secret 未配置")
    cache_key = f"{settings.feishu_app_id}:{settings.feishu_app_secret}"
    now = time.time()
    cached = _feishu_token_cache.get(cache_key)
    if cached and cached["expires_at"] > now + 30:
        return str(cached["token"])

    session = http_session or requests.Session()
    response = session.post(
        FEISHU_TENANT_TOKEN_URL,
        json={"app_id": settings.feishu_app_id, "app_secret": settings.feishu_app_secret},
        timeout=max(settings.default_timeout, 15),
    )
    response.raise_for_status()
    payload = response.json()
    if int(payload.get("code", 0)) != 0:
        raise RuntimeError(str(payload.get("msg") or "飞书 tenant_access_token 获取失败"))
    token = str(payload.get("tenant_access_token") or "").strip()
    if not token:
        raise RuntimeError("飞书 tenant_access_token 响应缺少 token")
    expire = int(payload.get("expire", 7200) or 7200)
    _feishu_token_cache[cache_key] = {"token": token, "expires_at": now + max(expire - 60, 60)}
    return token


def submit_feishu_convert_task(url: str, open_id: str) -> None:
    task = get_task_history_store().create_task(
        trigger_channel="feishu",
        source_type=detect_source_type(url),
        source_url=url,
    )
    _feishu_executor.submit(process_feishu_convert_task, url, open_id, str(task["task_id"]))


def process_feishu_convert_task(url: str, open_id: str, task_id: str | None = None) -> None:
    settings = get_settings()
    try:
        payload = execute_single_conversion(
            url=url,
            timeout=settings.default_timeout,
            save_html=False,
            output_target="fns",
            require_ai_success=True,
            trigger_channel="feishu",
            rerun_of_task_id=None,
            task_id=task_id,
        )
    except Exception as error:
        print(f"[feishu] conversion failed open_id={open_id}: {error}")
        send_feishu_message(open_id, f"转换失败：{error}")
        return

    ai_polish = payload.get("ai_polish") if isinstance(payload, dict) else {}
    if isinstance(ai_polish, dict):
        print(
            "[feishu] ai result "
            f"open_id={open_id} enabled={bool(ai_polish.get('enabled'))} "
            f"status={ai_polish.get('status')} "
            f"template_applied={bool(ai_polish.get('template_applied'))} "
            f"content_polished={bool(ai_polish.get('content_polished'))}"
        )
    print(
        "[feishu] conversion synced "
        f"open_id={open_id} path={payload['sync'].get('path') or payload['sync'].get('markdown_file') or '-'}"
    )

    if not settings.feishu_notify_on_complete:
        return

    title = str(payload["result"].get("title") or "转换完成")
    sync_path = str(payload["sync"].get("path") or payload["sync"].get("markdown_file") or "-")
    resolved_image_mode = str(
        payload["result"].get("image_mode")
        or payload.get("image_mode")
        or settings.image_mode
        or ""
    )
    image_mode = "S3 图床外链" if resolved_image_mode == "s3_hotlink" else "微信原链"
    send_feishu_message(
        open_id,
        "\n".join(
            [
                f"转换完成：{title}",
                f"同步路径：{sync_path}",
                f"图片模式：{image_mode}",
            ]
        ),
    )


def extract_single_wechat_url(text: str) -> tuple[str | None, int]:
    links = parse_links(urls_text=text or "")
    if not links:
        return None, 0
    unique_links: list[str] = []
    seen: set[str] = set()
    for item in links:
        if item in seen:
            continue
        seen.add(item)
        unique_links.append(item)
    return unique_links[0], len(unique_links)




def configure_feishu_webhook_state() -> dict[str, Any]:
    settings = get_settings()
    if not settings.feishu_enabled:
        state = {"status": "inactive", "message": "飞书 Bot 未启用", "webhook_url": settings.feishu_webhook_url or ""}
        update_feishu_webhook_state(state["status"], state["message"], webhook_url=state["webhook_url"])
        return state
    if not settings.feishu_enabled_and_configured or not settings.feishu_webhook_url:
        state = {"status": "error", "message": "飞书 Bot 配置不完整", "webhook_url": settings.feishu_webhook_url or ""}
        update_feishu_webhook_state(state["status"], state["message"], webhook_url=state["webhook_url"])
        return state
    state = {"status": "ready", "message": "请在飞书开放平台事件订阅中填写该 Webhook 地址", "webhook_url": settings.feishu_webhook_url}
    update_feishu_webhook_state(state["status"], state["message"], webhook_url=state["webhook_url"])
    return state


def extract_feishu_message_text(payload: dict[str, Any]) -> tuple[str, str | None, str | None]:
    if str(payload.get("type") or "") == "url_verification":
        return "", None, None
    event = payload.get("event") if isinstance(payload.get("event"), dict) else {}
    header = payload.get("header") if isinstance(payload.get("header"), dict) else {}
    if str(header.get("event_type") or "") != "im.message.receive_v1":
        return "", None, None

    message = event.get("message") if isinstance(event.get("message"), dict) else {}
    sender = event.get("sender") if isinstance(event.get("sender"), dict) else {}
    sender_id = sender.get("sender_id") if isinstance(sender.get("sender_id"), dict) else {}
    open_id = str(sender_id.get("open_id") or "").strip() or None
    chat_type = str(message.get("chat_type") or "").strip() or None
    if str(message.get("message_type") or "").strip() != "text":
        return "", open_id, chat_type
    content_raw = message.get("content")
    if isinstance(content_raw, str):
        try:
            content_obj = json.loads(content_raw)
        except ValueError:
            content_obj = {}
        if isinstance(content_obj, dict):
            return str(content_obj.get("text") or "").strip(), open_id, chat_type
    if isinstance(content_raw, dict):
        return str(content_raw.get("text") or "").strip(), open_id, chat_type
    return "", open_id, chat_type


def _telegram_api_url(token: str, method: str) -> str:
    return f"https://api.telegram.org/bot{token}/{method}"


def _extract_chat_preview(payload: dict[str, Any]) -> str:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return json.dumps(payload, ensure_ascii=False)[:200]
    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    if not isinstance(message, dict):
        return json.dumps(payload, ensure_ascii=False)[:200]
    content = message.get("content")
    if isinstance(content, list):
        preview = "".join(
            str(item.get("text") or "")
            for item in content
            if isinstance(item, dict)
        ).strip()
    else:
        preview = str(content or "").strip()
    return preview[:400]


def _copy_job(job: dict[str, Any]) -> dict[str, Any]:
    copied: dict[str, Any] = {}
    for key, value in job.items():
        if isinstance(value, list):
            copied[key] = list(value)
        elif isinstance(value, dict):
            copied[key] = dict(value)
        else:
            copied[key] = value
    return copied


job_store = JobStore()
_telegram_executor = ThreadPoolExecutor(max_workers=2)
_feishu_executor = ThreadPoolExecutor(max_workers=2)
_rerun_executor = ThreadPoolExecutor(max_workers=2)
_ingest_executor = ThreadPoolExecutor(max_workers=2)
_feishu_token_cache: dict[str, dict[str, Any]] = {}
