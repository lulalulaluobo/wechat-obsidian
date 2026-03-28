from __future__ import annotations

import os
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import requests

from app.config import get_settings
from app.core.pipeline import run_pipeline, sanitize_filename


URL_PATTERN = re.compile(r"https?://mp\.weixin\.qq\.com/s/[^\s)>]+", re.IGNORECASE)


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
            "results": [],
            "errors": [],
        }
        with self._lock:
            self._jobs[job_id] = payload
        self._executor.submit(self._run_batch_job, job_id, urls, output_dir, save_html, timeout, output_target)
        return payload.copy()

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return None if job is None else _copy_job(job)

    def _run_batch_job(
        self,
        job_id: str,
        urls: list[str],
        output_dir: Path,
        save_html: bool,
        timeout: int,
        output_target: str,
    ) -> None:
        ensure_runtime_environment()
        self._update(job_id, status="running")
        for url in urls:
            try:
                result = run_pipeline(url=url, output_base_dir=output_dir, save_html=save_html, timeout=timeout)
                sync = sync_result_to_output(result, output_target=output_target)
                self._append_result(
                    job_id,
                    {"url": url, "status": "success", "result": result, "sync": sync, "output_target": output_target},
                )
            except Exception as error:  # pragma: no cover - exercised in integration flow
                self._append_result(job_id, {"url": url, "status": "error", "error": str(error)})
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


def ensure_runtime_environment() -> None:
    settings = get_settings()
    os.environ["WECHAT_MD_R2_CONFIG_PATH"] = str(settings.default_r2_config_path)


def parse_links(urls: list[str] | None = None, urls_text: str | None = None, file_text: str | None = None) -> list[str]:
    raw_values: list[str] = []
    for source in urls or []:
        if source:
            raw_values.append(source.strip())
    for blob in (urls_text or "", file_text or ""):
        raw_values.extend(URL_PATTERN.findall(blob))
        raw_values.extend(
            line.strip()
            for line in blob.splitlines()
            if line.strip().startswith(("http://", "https://"))
        )

    deduped: list[str] = []
    seen: set[str] = set()
    for item in raw_values:
        if item not in seen:
            deduped.append(item)
            seen.add(item)
    return deduped


def read_uploaded_text(content: bytes) -> str:
    for encoding in ("utf-8", "utf-8-sig", "gbk"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    return content.decode("utf-8", errors="ignore")


def build_config_payload() -> dict[str, Any]:
    settings = get_settings()
    r2_config_exists = settings.default_r2_config_path.exists()
    default_output_target = build_output_target(None, settings)
    return {
        "default_output_dir": str(settings.default_output_dir),
        "default_r2_config_path": str(settings.default_r2_config_path),
        "r2_config_exists": r2_config_exists,
        "service_mode": "hybrid" if settings.fns_enabled else "local_only",
        "default_output_target": default_output_target,
        "auth_enabled": True,
        "fns_enabled": settings.fns_enabled,
        "fns_base_url": settings.fns_base_url,
        "fns_vault": settings.fns_vault,
        "fns_target_dir": settings.fns_target_dir,
        "current_user": {"username": settings.username},
        "cleanup_temp_on_success": settings.cleanup_temp_on_success,
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
