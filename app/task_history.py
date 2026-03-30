from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


TASK_STATUS_VALUES = {"queued", "running", "success", "error"}
TRIGGER_CHANNEL_VALUES = {"web", "telegram", "feishu"}
SOURCE_TYPE_VALUES = {"wechat", "zhihu", "web"}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class TaskHistoryStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def create_task(
        self,
        *,
        trigger_channel: str,
        source_type: str,
        source_url: str,
        rerun_of_task_id: str | None = None,
    ) -> dict[str, Any]:
        self._validate_channel(trigger_channel)
        self._validate_source_type(source_type)
        now = _utc_now()
        record = {
            "task_id": uuid.uuid4().hex,
            "trigger_channel": trigger_channel,
            "source_type": source_type,
            "source_url": str(source_url).strip(),
            "note_title": "",
            "status": "queued",
            "created_at": now,
            "updated_at": now,
            "error_message": "",
            "rerun_of_task_id": rerun_of_task_id or "",
            "sync_path": "",
        }
        self._append_record(record)
        return dict(record)

    def update_task(self, task_id: str, **fields: Any) -> dict[str, Any]:
        with self._lock:
            current = self._load_latest_records_unlocked().get(task_id)
            if current is None:
                raise KeyError(f"任务不存在: {task_id}")
            updated = dict(current)
            updated.update(fields)
            if "status" in updated:
                self._validate_status(str(updated["status"]))
            updated["updated_at"] = _utc_now()
            self._append_record_unlocked(updated)
        return dict(updated)

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        with self._lock:
            record = self._load_latest_records_unlocked().get(task_id)
        return None if record is None else dict(record)

    def list_tasks(
        self,
        *,
        trigger_channel: str | None = None,
        source_type: str | None = None,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        if trigger_channel:
            self._validate_channel(trigger_channel)
        if source_type:
            self._validate_source_type(source_type)
        if status:
            self._validate_status(status)
        with self._lock:
            records = list(self._load_latest_records_unlocked().values())
        records.sort(key=lambda item: (str(item.get("created_at") or ""), str(item.get("task_id") or "")), reverse=True)
        filtered = [
            record
            for record in records
            if (not trigger_channel or record["trigger_channel"] == trigger_channel)
            and (not source_type or record["source_type"] == source_type)
            and (not status or record["status"] == status)
        ]
        start = max(int(offset), 0)
        end = start + max(int(limit), 0)
        return {
            "items": [dict(item) for item in filtered[start:end]],
            "total": len(filtered),
            "limit": int(limit),
            "offset": int(offset),
        }

    def _append_record(self, record: dict[str, Any]) -> None:
        with self._lock:
            self._append_record_unlocked(record)

    def _append_record_unlocked(self, record: dict[str, Any]) -> None:
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    def _load_latest_records_unlocked(self) -> dict[str, dict[str, Any]]:
        if not self.path.exists():
            return {}
        latest: dict[str, dict[str, Any]] = {}
        with self.path.open("r", encoding="utf-8") as handle:
            for line in handle:
                text = line.strip()
                if not text:
                    continue
                payload = json.loads(text)
                task_id = str(payload.get("task_id") or "").strip()
                if not task_id:
                    continue
                latest[task_id] = payload
        return latest

    def _validate_status(self, value: str) -> None:
        if value not in TASK_STATUS_VALUES:
            raise ValueError(f"不支持的任务状态: {value}")

    def _validate_channel(self, value: str) -> None:
        if value not in TRIGGER_CHANNEL_VALUES:
            raise ValueError(f"不支持的触发方式: {value}")

    def _validate_source_type(self, value: str) -> None:
        if value not in SOURCE_TYPE_VALUES:
            raise ValueError(f"不支持的输入源类型: {value}")
